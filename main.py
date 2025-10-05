import os
import asyncio
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = 1293611593845706793  # <-- remplace par l'ID de ton serveur

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")

    try:
        guild = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild)
        print(f"✅ {len(synced)} commandes slash synchronisées pour {GUILD_ID}")
    except Exception as e:
        print(f"❌ Erreur de sync: {e}")

async def main():
    # Load all cogs
    for ext in [
        "cogs.auctions_core",
        "cogs.auctions_submit",
        "cogs.auctions_staff",
        "cogs.auctions_scheduler",
    ]:
        try:
            await bot.load_extension(ext)
            print(f"Loaded {ext}")
        except Exception as e:
            print(f"Failed to load {ext}: {e}")

    # Start the bot
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
