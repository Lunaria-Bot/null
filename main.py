# main.py
import os
import asyncio
import logging
import discord
from discord.ext import commands

# Active les logs (utile pour debug)
logging.basicConfig(level=logging.INFO)

# Récupère le token depuis les variables d'environnement
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("❌ DISCORD_TOKEN non défini dans les variables d'environnement.")

# Définition des intents
intents = discord.Intents.default()
intents.members = True  # nécessaire si tu veux accéder aux membres
intents.message_content = True  # utile si tu veux lire le contenu des messages

# Création du bot
bot = commands.Bot(command_prefix="!", intents=intents)

# Quand le bot est prêt
@bot.event
async def on_ready():
    logging.info(f"✅ Connecté en tant que {bot.user} (ID: {bot.user.id})")
    logging.info("------")

# Charger le cog auctions
async def load_cogs():
    await bot.load_extension("cogs.auctions")

async def main():
    async with bot:
        await load_cogs()
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
