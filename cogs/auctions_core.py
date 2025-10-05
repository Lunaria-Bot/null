# cogs/auction_core.py
import json
import discord
from discord.ext import commands
from config.settings import MAZOKU_BOT_ID, MAZOKU_CHANNEL_ID
from cogs.utils import Utils

class AuctionCore(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
            data["title"] = Utils.strip_discord_emojis(data["title"])

        desc = data.get("description", "") or ""
        owner_id = Utils.parse_owner_id_from_description(desc)

        if owner_id:
            await self.bot.redis.set(f"mazoku:card:{owner_id}", json.dumps(data), ex=600)
        else:
            # Optionally store last embed general
            await self.bot.redis.set("mazoku:last_embed", json.dumps(data), ex=300)
