import discord
from discord.ext import commands
from discord import app_commands

class StaffReview(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # First review queue channel message controls
    @commands.Cog.listener()
    async def on_ready(self):
        pass  # Could post guidelines or ensure setup

    @app_commands.command(name="auction-list", description="List auctions pending review.")
    async def auction_list(self, interaction: discord.Interaction):
        rows = await self.bot.pg.fetch("SELECT id, user_id, rarity, queue_type, currency, rate, status FROM auctions WHERE status='PENDING' ORDER BY id ASC")
        if not rows:
            await interaction.response.send_message("Aucune auction en attente.", ephemeral=True)
            return
        text = "\n".join([f"#{r['id']} — <@{r['user_id']}> — {r['rarity']} — {r['queue_type']} — {r['currency']} — {r['rate']}" for r in rows])
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="auction-review", description="Open staff review controls for pending auctions.")
    @app_commands.default_permissions(manage_messages=True)
    async def auction_review(self, interaction: discord.Interaction):
        rows = await self.bot.pg.fetch("SELECT id, user_id, rarity, queue_type, currency, rate FROM auctions WHERE status='PENDING' ORDER BY id ASC LIMIT 10")
        if not rows:
            await interaction.response.send_message("Rien à reviewer.", ephemeral=True)
            return

        embed = discord.Embed(title="Staff Review — Stage 1", color=discord.Color.orange())
        for r in rows:
            embed.add_field(name=f"Auction #{r['id']}", value=f"User: <@{r['user_id']}> | {r['rarity']} | {r['queue_type']} | {r['currency']} | {r['rate']}", inline=False)
        view = ReviewView(self.bot, stage=1)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ReviewView(discord.ui.View):
    def __init__(self, bot, stage: int):
        super().__init__(timeout=600)
        self.bot = bot
        self.stage = stage

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_decision(interaction, "accept")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ReasonModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        reason = modal.value or "No reason provided."
        await self._handle_decision(interaction, "deny", reason)

    async def _handle_decision(self, interaction: discord.Interaction, decision: str, reason: str = None):
        # Find a single pending auction to decide (for demo: top oldest)
        row = await self.bot.pg.fetchrow("SELECT id, user_id FROM auctions WHERE status='PENDING' ORDER BY id ASC LIMIT 1")
        if not row:
            await interaction.followup.send("Plus d’auctions à traiter.", ephemeral=True)
            return
        auction_id = row["id"]
        user_id = row["user_id"]

        await self.bot.pg.execute(
            "INSERT INTO reviews (auction_id, stage, reviewer_id, decision, reason) VALUES ($1,$2,$3,$4,$5)",
            auction_id, self.stage, interaction.user.id, decision, reason
        )
        if decision == "accept":
            # Move to READY for batch
            await self.bot.pg.execute("UPDATE auctions SET status='READY' WHERE id=$1", auction_id)
            await interaction.followup.send(f"Auction #{auction_id} acceptée.", ephemeral=True)
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(f"Your card has been accepted.")
            except Exception:
                pass
        else:
            await self.bot.pg.execute("UPDATE auctions SET status='DENIED' WHERE id=$1", auction_id)
            await interaction.followup.send(f"Auction #{auction_id} refusée.", ephemeral=True)
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(f"Your card has been refused. Reason: {reason}")
            except Exception:
                pass

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
