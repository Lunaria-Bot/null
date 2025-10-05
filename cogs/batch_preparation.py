import discord
from discord.ext import commands
from discord import app_commands
from .auction_core import get_or_create_today_batch

class BatchPreparation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="batch-fill", description="Fill batch: 15 Normal, then extras Normal/Card Maker until lock.")
    @app_commands.default_permissions(manage_messages=True)
    async def batch_fill(self, interaction: discord.Interaction):
        bid = await get_or_create_today_batch(self.bot.pg)

        normals = await self.bot.pg.fetch("""
            SELECT id FROM auctions WHERE status='READY' AND queue_type='NORMAL' ORDER BY id ASC LIMIT 15
        """)
        extras = await self.bot.pg.fetch("""
            SELECT id FROM auctions WHERE status='READY' AND queue_type IN ('NORMAL','CARD_MAKER')
            ORDER BY id ASC OFFSET 15
        """)

        position = 1
        for row in normals:
            await self.bot.pg.execute("INSERT INTO batch_items (batch_id, auction_id, position) VALUES ($1,$2,$3)", bid, row["id"], position)
            position += 1
        for row in extras:
            await self.bot.pg.execute("INSERT INTO batch_items (batch_id, auction_id, position) VALUES ($1,$2,$3)", bid, row["id"], position)
            position += 1

        await interaction.response.send_message(f"Batch #{bid} rempli avec {position-1} items.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(BatchPreparation(bot))
