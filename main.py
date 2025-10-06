import os
import logging
import discord
from discord.ext import commands
import asyncpg
import redis.asyncio as aioredis

from cogs.auction_core import init_db  # DB bootstrap

logging.basicConfig(level=logging.INFO)

INTENTS = discord.Intents.default()
INTENTS.message_content = True  # required for Mazoku message capture

class AuctionBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.pg = None
        self.redis = None
        self.guild_id = int(os.getenv("GUILD_ID"))

        # IDs from environment variables
        self.mazoku_bot_id = int(os.getenv("MAZOKU_BOT_ID"))
        self.mazoku_channel_id = int(os.getenv("MAZOKU_CHANNEL_ID"))
        self.ping_channel_id = int(os.getenv("PING_CHANNEL_ID"))
        self.queue_skip_id = int(os.getenv("QUEUE_SKIP_ID"))
        self.queue_normal_id = int(os.getenv("QUEUE_NORMAL_ID"))
        self.queue_cm_id = int(os.getenv("QUEUE_CM_ID"))
        self.forum_common_id = int(os.getenv("FORUM_COMMON_ID"))
        self.forum_rare_id = int(os.getenv("FORUM_RARE_ID"))
        self.forum_sr_id = int(os.getenv("FORUM_SR_ID"))
        self.forum_ssr_id = int(os.getenv("FORUM_SSR_ID"))
        self.forum_ur_id = int(os.getenv("FORUM_UR_ID"))
        self.forum_cm_id = int(os.getenv("FORUM_CM_ID"))

        # Nouveau : channel de log
        self.log_channel_id = int(os.getenv("LOG_CHANNEL_ID"))

    async def setup_hook(self):
        # Connect databases
        self.pg = await asyncpg.create_pool(os.getenv("POSTGRES_URL"))
        self.redis = aioredis.from_url(
            os.getenv("REDIS_URL"),
            encoding="utf-8",
            decode_responses=True
        )

        # Initialize database schema
        await init_db(self.pg)

        # Load extensions (do not load utils as an extension)
        await self.load_extension("cogs.auction_core")
        await self.load_extension("cogs.submit")
        await self.load_extension("cogs.staff_review")
        await self.load_extension("cogs.batch_preparation")
        await self.load_extension("cogs.scheduler")

        # Auto-sync application commands to the target guild
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logging.info(f"Synced {len(synced)} commands to guild {self.guild_id}")

    async def close(self):
        await super().close()
        if self.pg:
            await self.pg.close()
        if self.redis:
            await self.redis.aclose()  # use aclose() (close() is deprecated)

bot = AuctionBot()

@bot.tree.command(name="sync", description="Force resync of slash commands")
@commands.has_permissions(administrator=True)
async def sync_cmd(interaction: discord.Interaction):
    # Defer immediately to avoid interaction timeout
    await interaction.response.defer(ephemeral=True)

    guild = discord.Object(id=interaction.guild_id)
    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)

    await interaction.followup.send(f"Synced {len(synced)} commands.")

def main():
    bot.run(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    main()
