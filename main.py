async def main():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

    for ext in [
        "cogs.auctions_core",
        "cogs.auctions_utils",
        "cogs.auctions_submit",
        "cogs.auctions_staff",
        "cogs.auctions_scheduler",
    ]:
        await bot.load_extension(ext)

    await bot.start(os.getenv("DISCORD_TOKEN"))
