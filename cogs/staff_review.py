import discord
from discord.ext import commands
from discord import app_commands

class StaffReview(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="auction-list", description="List auctions pending review.")
    async def auction_list(self, interaction: discord.Interaction):
        rows = await self.bot.pg.fetch("""
            SELECT id, user_id, rarity, queue_type, currency, rate, series, version, title
            FROM auctions WHERE status='PENDING' ORDER BY id ASC
        """)
        if not rows:
            await interaction.response.send_message("Aucune auction en attente.", ephemeral=True)
            return
        embed = discord.Embed(title="Pending Auctions", color=discord.Color.orange())
        for r in rows:
            name = r["title"] or f"{r['series']} v{r['version']}"
            embed.add_field(
                name=f"#{r['id']} — {name}",
                value=f"User: <@{r['user_id']}> | {r['rarity']} | {r['queue_type']} | {r['currency']} | {r['rate']}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="auction-review", description="Open staff review controls.")
    @app_commands.default_permissions(manage_messages=True)
    async def auction_review(self, interaction: discord.Interaction):
        row = await self.bot.pg.fetchrow("""
            SELECT id, user_id, rarity, queue_type, currency, rate, series, version, title
            FROM auctions WHERE status='PENDING' ORDER BY id ASC LIMIT 1
        """)
        if not row:
            await interaction.response.send_message("Rien à reviewer.", ephemeral=True)
            return
        name = row["title"] or f"{row['series']} v{row['version']}"
        embed = discord.Embed(title=f"Review Auction #{row['id']}", description=name, color=discord.Color.blurple())
        embed.add_field(name="Seller", value=f"<@{row['user_id']}>", inline=True)
        embed.add_field(name="Rarity", value=row["rarity"], inline=True)
        embed.add_field(name="Queue", value=row["queue_type"], inline=True)
        embed.add_field(name="Currency", value=row["currency"], inline=True)
        embed.add_field(name="Rate", value=row["rate"], inline=True)
        await interaction.response.send_message(embed=embed, view=ReviewView(self.bot, row["id"], row["user_id"]), ephemeral=True)

class ReviewView(discord.ui.View):
    def __init__(self, bot, auction_id: int, user_id: int):
        super().__init__(timeout=600)
        self.bot = bot
        self.auction_id = auction_id
        self.user_id = user_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.pg.execute(
            "INSERT INTO reviews (auction_id, stage, reviewer_id, decision) VALUES ($1,$2,$3,$4)",
            self.auction_id, 1, interaction.user.id, "accept"
        )
        await self.bot.pg.execute("UPDATE auctions SET status='READY' WHERE id=$1", self.auction_id)
        await interaction.response.send_message(f"Auction #{self.auction_id} acceptée.", ephemeral=True)
        try:
            user = await self.bot.fetch_user(self.user_id)
            await user.send("Your card has been accepted.")
        except Exception:
            pass
        self.stop()

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ReasonModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        reason = modal.value or "No reason provided."
        await self.bot.pg.execute(
            "INSERT INTO reviews (auction_id, stage, reviewer_id, decision, reason) VALUES ($1,$2,$3,$4,$5)",
            self.auction_id, 1, interaction.user.id, "deny", reason
        )
        await self.bot.pg.execute("UPDATE auctions SET status='DENIED' WHERE id=$1", self.auction_id)
        await interaction.followup.send(f"Auction #{self.auction_id} refusée.", ephemeral=True)
        try:
            user = await self.bot.fetch_user(self.user_id)
            await user.send(f"Your card has been refused. Reason: {reason}")
        except Exception:
            pass
        self.stop()

class ReasonModal(discord.ui.Modal, title="Reason"):
    def __init__(self):
        super().__init__(title="Reason")
        self.input = discord.ui.TextInput(label="Enter reason", required=False, max_length=200)
        self.add_item(self.input)
        self.value = None
    async def on_submit(self, interaction: discord.Interaction):
        self.value = str(self.input.value).strip()
        await interaction.response.send_message("Raison enregistrée.", ephemeral=True)
        self.stop()

async def setup(bot: commands.Bot):
    await bot.add_cog(StaffReview(bot))
