import os
import json
import asyncpg
import redis.asyncio as redis
import discord
from discord.ext import commands
from discord import app_commands

from .auctions_utils import strip_discord_emojis

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

AUCTION_CHANNEL_ID = get_int_env("AUCTION_CHANNEL_ID", required=False, default=0)


class AuctionsCore(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pg_pool: asyncpg.Pool | None = None
        self.redis: redis.Redis | None = None

    async def ensure_submissions_schema(self, conn: asyncpg.Connection):
        # Create table baseline if missing
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

        # Idempotent migration: add any missing columns
        # Note: IF NOT EXISTS is supported in PostgreSQL 9.6+ for ADD COLUMN.
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS user_id BIGINT NOT NULL;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS card JSONB NOT NULL;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS currency TEXT;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS rate TEXT;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS queue TEXT;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS batch_id INT;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS fees_paid BOOLEAN;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS deny_reason TEXT;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'submitted';")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMP;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS queue_message_id BIGINT;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS queue_channel_id BIGINT;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS queue_thread_id BIGINT;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS released_message_id BIGINT;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS released_channel_id BIGINT;")
        await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS closed BOOLEAN DEFAULT FALSE;")

        # Optional: indexes for common queries
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
            # Ensure schema is up to date even if the table already exists
            await self.ensure_submissions_schema(conn)

    async def cog_unload(self):
        if self.redis:
            await self.redis.close()
        if self.pg_pool:
            await self.pg_pool.close()

    # Capture Mazoku card embeds and cache by owner
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
        desc = data.get("description", "") or ""
        # sanitize title for later
        if "title" in data and data["title"]:
            data["title"] = strip_discord_emojis(data["title"])
        if "Owned by <@" in desc:
            try:
                start = desc.find("Owned by <@") + len("Owned by <@")
                end = desc.find(">", start)
                owner_id = int(desc[start:end])
                await self.redis.set(f"mazoku:card:{owner_id}", json.dumps(data), ex=600)
            except Exception as e:
                print("❗ Error parsing owner_id:", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionsCore(bot))
