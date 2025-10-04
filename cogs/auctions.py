# cogs/auctions.py
import os
import json
from datetime import datetime, timezone
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncpg
import redis.asyncio as redis

# ---------- Safe environment loader ----------
def get_int_env(name: str, required: bool = True, default: int = 0) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        raise RuntimeError(f"Environment variable {name} must be an integer, got: {val}")

# --- Required IDs ---
GUILD_ID = get_int_env("GUILD_ID", required=True)
MAZOKU_BOT_ID = 1242388858897956906
MAZOKU_CHANNEL_ID = 1303054862447022151

# --- Optional IDs ---
AUCTION_CHANNEL_ID = get_int_env("AUCTION_CHANNEL_ID", required=False, default=0)

# --- Database / Redis ---
POSTGRES_DSN = {
    "user": os.getenv("POSTGRES_USER") or os.getenv("PGUSER"),
    "password": os.getenv("POSTGRES_PASSWORD") or os.getenv("PGPASSWORD"),
    "database": os.getenv("POSTGRES_DB") or os.getenv("PGDATABASE"),
    "host": os.getenv("POSTGRES_HOST") or os.getenv("PGHOST"),
    "port": int(os.getenv("POSTGRES_PORT") or os.getenv("PGPORT", "5432")),
}
REDIS_URL = os.getenv("REDIS_URL")


class Auctions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.redis = None
        self.pg_pool = None
        self.check_auctions.start()

    async def cog_load(self):
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self.pg_pool = await asyncpg.create_pool(**POSTGRES_DSN)
        async with self.pg_pool.acquire() as conn:
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                card JSONB NOT NULL,
                currency TEXT,
                rate TEXT,
                status TEXT NOT NULL DEFAULT 'submitted',
                scheduled_for TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """)

    async def cog_unload(self):
        if self.redis:
            await self.redis.close()
        if self.pg_pool:
            await self.pg_pool.close()
        self.check_auctions.cancel()

    # --- DEBUG: log Mazoku messages in the given channel ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            message.author.bot
            and message.author.id == MAZOKU_BOT_ID
            and message.channel.id == MAZOKU_CHANNEL_ID
        ):
            if message.embeds:
                embed = message.embeds[0]
                print("📥 Mazoku message reçu :", embed.to_dict())

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if (
            after.author.bot
            and after.author.id == MAZOKU_BOT_ID
            and after.channel.id == MAZOKU_CHANNEL_ID
        ):
            if after.embeds:
                embed = after.embeds[0]
                print("✏️ Mazoku message modifié :", embed.to_dict())

    # --- Slash command: auction-submit ---
    @app_commands.command(name="auction-submit", description="Submit your Mazoku card for auction")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_submit(self, interaction: discord.Interaction):
        # Pour l’instant, on ne fait que confirmer que la commande marche
        await interaction.response.send_message(
            "Commande `/auction-submit` détectée. Les logs des messages Mazoku sont visibles en console.",
            ephemeral=True
        )

    # --- Scheduler loop (placeholder) ---
    @tasks.loop(minutes=1)
    async def check_auctions(self):
        now = datetime.now(timezone.utc)
        # Ici tu pourras garder ta logique d’enchères plus tard
        pass


async def setup(bot):
    await bot.add_cog(Auctions(bot))
