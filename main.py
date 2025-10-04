# main.py
import os
import asyncio
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"📦 Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Sync error: {e}")


async def main():
    async with bot:
        await bot.load_extension("cogs.auctions")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
