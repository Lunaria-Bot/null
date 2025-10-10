import discord
from discord.ext import commands
from discord import app_commands

class AuctionStatus(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="auction-status", description="Check the status of an auction by ID.")
    async def auction_status(self, interaction: discord.Interaction, auction_id: int):
        rec = await self.bot.pg.fetchrow(
            "SELECT id, title, rarity, queue_type, status, currency, rate, user_id, image_url "
            "FROM auctions WHERE id=$1",
            auction_id
        )
        if not rec:
            return await interaction.response.send_message(f"Auction #{auction_id} not found.", ephemeral=True)

        embed = discord.Embed(
            title=f"Auction #{rec['id']} — {rec['title'] or 'Untitled'}",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Rarity", value=rec["rarity"], inline=True)
        embed.add_field(name="Queue", value=rec["queue_type"], inline=True)
        embed.add_field(name="Currency", value=rec["currency"], inline=True)
        embed.add_field(name="Rate", value=rec["rate"] or "—", inline=True)
        embed.add_field(name="Status", value=rec["status"], inline=True)
        embed.add_field(name="Seller", value=f"<@{rec['user_id']}>", inline=False)
        if rec["image_url"]:
            embed.set_thumbnail(url=rec["image_url"])

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionStatus(bot))
