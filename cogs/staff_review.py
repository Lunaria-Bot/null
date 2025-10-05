# cogs/staff_review.py
import discord
from discord.ext import commands

class StaffReview(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Ici on peut ajouter des commandes staff pour lister, filtrer, etc.
    @commands.command(name="list_pending")
    @commands.has_permissions(manage_messages=True)
    async def list_pending(self, ctx: discord.Context):
        rows = await self.bot.pg.fetch("SELECT id, user_id, queue FROM submissions WHERE status='Pending' ORDER BY id DESC LIMIT 25")
        if not rows:
            return await ctx.send("Aucune soumission en attente.")
        lines = [f"#{r['id']} by <@{r['user_id']}> [{r['queue']}]" for r in rows]
        await ctx.send("\n".join(lines))
