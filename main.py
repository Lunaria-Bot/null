# main.py
import asyncio
import logging
import asyncpg
import discord
from discord.ext import commands
import redis.asyncio as aioredis  # on utilise redis.asyncio à la place d'aioredis

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

# ID de ton serveur Discord
GUILD_ID = 1293611593845706793


class AuctionBot(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(command_prefix="!", intents=intents, **kwargs)
        self.pg = None
        self.redis = None

    async def setup_hook(self):
        # Connexion PostgreSQL
        try:
            self.pg = await asyncpg.create_pool(dsn=PG_DSN, min_size=1, max_size=5)
            logging.info("✅ PostgreSQL pool initialized.")
        except Exception as e:
            logging.error(f"❌ PostgreSQL connection failed: {e}")

        # Connexion Redis
        try:
            self.redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
            logging.info("✅ Redis connected.")
        except Exception as e:
            logging.error(f"❌ Redis connection failed: {e}")

        # Charger les cogs
        await self.add_cog(Utils(self))
        await self.add_cog(AuctionCore(self))
        await self.add_cog(Submit(self))
        await self.add_cog(StaffReview(self))
        await self.add_cog(BatchPreparation(self))
        await self.add_cog(Scheduler(self))

        # Synchronisation des commandes slash (forcée sur ton serveur)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logging.info(f"✅ Slash commands synced to guild {GUILD_ID}")

    async def close(self):
        await super().close()
        if self.pg:
            await self.pg.close()
        if self.redis:
            await self.redis.close()


bot = AuctionBot()

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
