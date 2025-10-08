import discord
from discord.ext import commands

class AuctionListener(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignorer les messages du bot
        if message.author.bot:
            return

        # Vérifier si on est dans un thread
        if not isinstance(message.channel, discord.Thread):
            return

        # Normaliser le contenu
        content = message.content.lower().strip()

        # Si quelqu’un écrit "accept" ou "accepted"
        if content in ("accept", "accepted"):
            try:
                await message.channel.edit(archived=True, locked=True)
                await message.channel.send("✅ Auction accepted. Thread locked.")
            except Exception as e:
                print("Erreur lors du lock du thread:", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionListener(bot))
