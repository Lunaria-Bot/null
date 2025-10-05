import os
import asyncio
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
     try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"✅ {len(synced)} commandes synchronisées pour le serveur {GUILD_ID}")
    except Exception as e:
        print(f"❌ Erreur de sync: {e}")
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")

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
