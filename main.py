# main.py
import asyncio
import logging
import asyncpg
import redis.asyncio as aioredis
import discord
from discord.ext import commands

from config.settings import BOT_TOKEN, PG_DSN, REDIS_URL
from cogs.auction_core import AuctionCore
from cogs.submit import Submit
from cogs.staff_review import StaffReview
from cogs.batch_preparation import BatchPreparation
from cogs.scheduler import Scheduler
from cogs.utils import Utils

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class AuctionBot(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(command_prefix="!", intents=intents, **kwargs)
        self.pg = None
        self.redis = None

    async def setup_hook(self):
        # DB connections
        self.pg = await asyncpg.create_pool(dsn=PG_DSN, min_size=1, max_size=5)
        self.redis = await aioredis.from_url(REDIS_URL, decode_responses=True)

        # Load cogs
        await self.add_cog(Utils(self))
        await self.add_cog(AuctionCore(self))
        await self.add_cog(Submit(self))
        await self.add_cog(StaffReview(self))
        await self.add_cog(BatchPreparation(self))
        await self.add_cog(Scheduler(self))

        # Sync slash commands
        await self.tree.sync()

    async def close(self):
        await super().close()
        if self.pg:
            await self.pg.close()
        if self.redis:
            await self.redis.close()

bot = AuctionBot()

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
