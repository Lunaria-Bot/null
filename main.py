# main.py
import os
import asyncio
import logging
import discord
from discord.ext import commands

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

intents = discord.Intents.default()
intents.members = True          # requis pour guild.get_member, on_member_join
intents.message_content = True  # requis si tu veux lire le contenu des messages

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logging.info(f"✅ Connecté en tant que {bot.user} (ID: {bot.user.id})")
    try:
        # Synchronisation des slash commands uniquement sur ton serveur
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        logging.info(f"📦 {len(synced)} commandes slash synchronisées avec le serveur {GUILD_ID}.")
    except Exception as e:
        logging.error(f"❌ Erreur de synchronisation des commandes: {e}")
    logging.info("------")

async def load_cogs():
    await bot.load_extension("cogs.auctions")

async def main():
    async with bot:
        await load_cogs()
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
