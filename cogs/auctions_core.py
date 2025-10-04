import os
import json
import asyncpg
import redis.asyncio as redis
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timezone

from .auctions_utils import (
    strip_discord_emojis,
    extract_first_emoji_id,
    RARITY_CHANNELS,
    CARDMAKER_CHANNEL_ID,
)

def get_int_env(name: str, required: bool = True, default: int = 0) -> int:
    val = os.getenv(name)
    if val is None:
        if required:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return default
    try:
        return int(val)
    except ValueError:
        raise RuntimeError(f"Environment variable {name} must be an integer, got: {val}")

GUILD_ID = get_int_env("GUILD_ID", required=True)

MAZOKU_BOT_ID = 1242388858897956906
MAZOKU_CHANNEL_ID = 1303054862447022151

POSTGRES_DSN = {
    "user": os.getenv("POSTGRES_USER") or os.getenv("PGUSER"),
    "password": os.getenv("POSTGRES_PASSWORD") or os.getenv("PGPASSWORD"),
    "database": os.getenv("POSTGRES_DB") or os.getenv("PGDATABASE"),
    "host": os.getenv("POSTGRES_HOST") or os.getenv("PGHOST"),
    "port": int(os.getenv("POSTGRES_PORT") or os.getenv("PGPORT", "5432")),
}
REDIS_URL = os.getenv("REDIS_URL")


class AuctionsCore(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pg_pool: asyncpg.Pool | None = None
        self.redis: redis.Redis | None = None
        self.scheduler_loop.start()

    async def ensure_submissions_schema(self, conn: asyncpg.Connection):
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            card JSONB NOT NULL,
            currency TEXT,
            rate TEXT,
            queue TEXT,
            batch_id INT,
            fees_paid BOOLEAN,
            deny_reason TEXT,
            status TEXT NOT NULL DEFAULT 'submitted',
            scheduled_for TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            queue_message_id BIGINT,
            queue_channel_id BIGINT,
            queue_thread_id BIGINT,
            released_message_id BIGINT,
            released_channel_id BIGINT,
            closed BOOLEAN DEFAULT FALSE
        );
        """)
        # Index utiles
        await conn.execute("CREATE INDEX IF NOT EXISTS submissions_status_idx ON submissions(status);")
        await conn.execute("CREATE INDEX IF NOT EXISTS submissions_scheduled_for_idx ON submissions(scheduled_for);")
        await conn.execute("CREATE INDEX IF NOT EXISTS submissions_queue_idx ON submissions(queue);")
        await conn.execute("CREATE INDEX IF NOT EXISTS submissions_batch_id_idx ON submissions(batch_id);")

    async def cog_load(self):
        if not REDIS_URL:
            raise RuntimeError("REDIS_URL is not set")
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self.pg_pool = await asyncpg.create_pool(**POSTGRES_DSN)
        async with self.pg_pool.acquire() as conn:
            await self.ensure_submissions_schema(conn)

    async def cog_unload(self):
        if self.redis:
            await self.redis.close()
        if self.pg_pool:
            await self.pg_pool.close()
        self.scheduler_loop.cancel()

    # --- Capture des cartes Mazoku ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            message.author.bot
            and message.author.id == MAZOKU_BOT_ID
            and message.channel.id == MAZOKU_CHANNEL_ID
        ):
            await self._process_mazoku_embed(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if (
            after.author.bot
            and after.author.id == MAZOKU_BOT_ID
            and after.channel.id == MAZOKU_CHANNEL_ID
        ):
            await self._process_mazoku_embed(after)

    async def _process_mazoku_embed(self, message: discord.Message):
        if not message.embeds:
            return
        embed = message.embeds[0]
        data = embed.to_dict()
        if "title" in data and data["title"]:
            data["title"] = strip_discord_emojis(data["title"])
        desc = data.get("description", "") or ""
        if "Owned by <@" in desc:
            try:
                start = desc.find("Owned by <@") + len("Owned by <@")
                end = desc.find(">", start)
                owner_id = int(desc[start:end])
                await self.redis.set(f"mazoku:card:{owner_id}", json.dumps(data), ex=600)
            except Exception as e:
                print("❗ Error parsing owner_id:", e)

    # --- Scheduler ---
    @tasks.loop(minutes=1)
    async def scheduler_loop(self):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        async with self.pg_pool.acquire() as conn:
            # 🔒 Fermer les anciens posts
            await self.close_old_batches(conn, now)

            # Publier les nouveaux
            rows = await conn.fetch(
                "SELECT * FROM submissions "
                "WHERE status='accepted' AND scheduled_for <= $1 "
                "AND released_message_id IS NULL",
                now
            )

        for row in rows:
            try:
                await self.publish_submission(row)
            except Exception as e:
                print(f"❌ Erreur publication submission {row['id']}:", e)

    async def publish_submission(self, row):
        """Publie une carte acceptée dans le forum de rareté."""
        card_dict = row["card"] if isinstance(row["card"], dict) else json.loads(row["card"])
        card_embed = discord.Embed.from_dict(card_dict)

        # Déterminer le forum cible
        if row["queue"] == "cardmaker":
            target_channel_id = CARDMAKER_CHANNEL_ID
        else:
            rarity_id = extract_first_emoji_id(card_embed.description)
            target_channel_id = RARITY_CHANNELS.get(rarity_id)

        channel = self.bot.get_channel(target_channel_id)
        if not channel:
            print(f"❌ Forum introuvable pour submission {row['id']}")
            return

        # Créer un post dans le forum
        if isinstance(channel, discord.ForumChannel):
            thread = await channel.create_thread(
                name=f"Auction #{row['id']} – {card_embed.title or 'Card'}",
                embed=card_embed
            )
            msg = thread.message
            thread_id = thread.id
        else:
            msg = await channel.send(embed=card_embed)
            thread = await msg.create_thread(name=f"Auction #{row['id']} – {card_embed.title or 'Card'}")
            thread_id = thread.id

        # Mettre à jour la DB
        async with self.pg_pool.acquire() as conn:
            await conn.execute(
                "UPDATE submissions SET released_message_id=$1, released_channel_id=$2, queue_thread_id=$3 WHERE id=$4",
                msg.id, channel.id, thread_id, row["id"]
            )

    async def close_old_batches(self, conn, now: datetime):
        """Ferme (lock + archive) les anciens threads déjà publiés."""
        rows = await conn.fetch(
            "SELECT id, released_channel_id, queue_thread_id "
            "FROM submissions "
            "WHERE status='accepted' AND released_message_id IS NOT NULL AND closed=FALSE "
            "AND scheduled_for < $1",
            now
        )
        for row in rows:
            channel = self.bot.get_channel(row["released_channel_id"])
            if not channel:
                continue
            if isinstance(channel, discord.ForumChannel):
                thread = channel.get_thread(row["queue_thread_id"])
                if thread and not thread.locked:
                    try:
                        await thread.edit(locked=True, archived=True)
                        print(f"🔒 Thread {thread.id} fermé pour submission {row['id']}")
                    except Exception as e:
                        print(f"❌ Erreur fermeture thread {row['id']}:", e)
            await conn.execute("UPDATE submissions SET closed=TRUE WHERE id=$1", row["id"])

    @scheduler_loop.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionsCore(bot))
