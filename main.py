import os
import asyncio
import logging
import discord
from discord.ext import commands
import asyncpg
import redis.asyncio as aioredis

logging.basicConfig(level=logging.INFO)

INTENTS = discord.Intents.default()
INTENTS.message_content = True  # Nécessaire pour la capture Mazoku

class AuctionBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.pg = None
        self.redis = None
        self.guild_id = int(os.getenv("GUILD_ID"))
        # IDs
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

    async def setup_hook(self):
        # DB connections
        self.pg = await asyncpg.create_pool(os.getenv("POSTGRES_URL"))
        self.redis = aioredis.from_url(os.getenv("REDIS_URL"), encoding="utf-8", decode_responses=True)

        await self.load_extension("cogs.auction_core")
        await self.load_extension("cogs.submit")
        await self.load_extension("cogs.staff_review")
        await self.load_extension("cogs.batch_preparation")
        await self.load_extension("cogs.scheduler")
        await self.load_extension("cogs.utils")

        # Global slash registration
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def close(self):
        await super().close()
        if self.pg:
            await self.pg.close()
        if self.redis:
            await self.redis.close()

def main():
    bot = AuctionBot()
    bot.run(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    main()
