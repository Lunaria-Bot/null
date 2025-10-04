import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone

from .auctions_core import GUILD_ID
from .auctions_utils import next_daily_release

class StaffReviewView(discord.ui.View):
    def __init__(self, bot: commands.Bot, submission_id: int, queue_choice: str):
        super().__init__(timeout=None)
        self.bot = bot
        self.submission_id = submission_id
        self.queue_choice = queue_choice

    async def update_embed(self, interaction: discord.Interaction, extra_text: str,
                           color: discord.Color | None = None, remove_view: bool = False):
        if not interaction.message.embeds:
            return
        embed = interaction.message.embeds[0].copy()
        desc = embed.description or ""
        desc += f"\n{extra_text}"
        embed.description = desc
        if color:
            embed.color = color
        await interaction.message.edit(embed=embed, view=None if remove_view else self)

    async def notify_user(self, status: str, reason: str | None = None, batch_id: int | None = None):
        core = self.bot.get_cog("AuctionsCore")
        if core is None or core.pg_pool is None:
            return
        async with core.pg_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT user_id FROM submissions WHERE id=$1", self.submission_id)
        if not row:
            return
        user = await self.bot.fetch_user(row["user_id"])
        embed = discord.Embed(color=discord.Color.green() if status == "accepted" else discord.Color.red())
        if status == "accepted":
            embed.title = "✅ Your card has been accepted!"
            embed.description = f"Added to Batch {batch_id}"
        else:
            embed.title = "❌ Your card has been denied."
            if reason:
                embed.description = f"Reason: {reason}"
        try:
            await user.send(embed=embed)
        except:
            pass

    @discord.ui.button(label="Fees Paid", style=discord.ButtonStyle.green)
    async def fees_paid_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        core = self.bot.get_cog("AuctionsCore")
        async with core.pg_pool.acquire() as conn:
            await conn.execute("UPDATE submissions SET fees_paid = TRUE WHERE id=$1", self.submission_id)
        await self.update_embed(interaction, "Fee paid 💵")
        await interaction.response.send_message("✅ Fees marked as paid", ephemeral=True)

    @discord.ui.button(label="Fees Not Paid", style=discord.ButtonStyle.red)
    async def fees_not_paid_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        core = self.bot.get_cog("AuctionsCore")
        async with core.pg_pool.acquire() as conn:
            await conn.execute("UPDATE submissions SET fees_paid = FALSE WHERE id=$1", self.submission_id)
        await self.update_embed(interaction, "❌ Fees not paid")
        await interaction.response.send_message("❌ Fees marked as not paid", ephemeral=True)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        core = self.bot.get_cog("AuctionsCore")
        # Assign batch and schedule
        now_utc = datetime.now(timezone.utc)
        release_at = next_daily_release(now_utc)
        async with core.pg_pool.acquire() as conn:
            if self.queue_choice == "normal":
                last = await conn.fetchrow(
                    "SELECT batch_id, COUNT(*) AS count FROM submissions "
                    "WHERE queue='normal' AND status='accepted' "
                    "GROUP BY batch_id ORDER BY batch_id DESC LIMIT 1"
                )
                if (not last) or (last["batch_id"] is None) or (last["count"] >= 15):
                    last_id_row = await conn.fetchrow(
                        "SELECT COALESCE(MAX(batch_id), 0) AS max_id FROM submissions WHERE queue='normal'"
                    )
                    batch_id = (last_id_row["max_id"] or 0) + 1
                else:
                    batch_id = last["batch_id"]
            else:
                batch_id = 1

            await conn.execute(
                "UPDATE submissions SET status='accepted', batch_id=$1, scheduled_for=$2 WHERE id=$3",
                batch_id, release_at, self.submission_id
            )

        await self.update_embed(
            interaction,
            f"✅ Card accepted (Batch {batch_id}) • Release: {release_at.strftime('%d/%m/%y %H:%M UTC')}",
            color=discord.Color.green(),
            remove_view=True
        )
        await self.notify_user("accepted", batch_id=batch_id)
        await interaction.response.send_message(f"Card added to Batch {batch_id}", ephemeral=False)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        core = self.bot.get_cog("AuctionsCore")
        async with core.pg_pool.acquire() as conn:
            await conn.execute("UPDATE submissions SET status='denied' WHERE id=$1", self.submission_id)
        await self.update_embed(interaction, "❌ Card denied", color=discord.Color.red(), remove_view=True)
        await self.notify_user("denied")
        await interaction.response.send_message("Card denied", ephemeral=False)

    @discord.ui.button(label="Deny with reason", style=discord.ButtonStyle.gray)
    async def deny_reason_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DenyReasonModal(self.bot, self.submission_id))


class DenyReasonModal(discord.ui.Modal, title="Deny with reason"):
    reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, required=True)

    def __init__(self, bot: commands.Bot, submission_id: int):
        super().__init__()
        self.bot = bot
        self.submission_id = submission_id

    async def on_submit(self, interaction: discord.Interaction):
        core = self.bot.get_cog("AuctionsCore")
        async with core.pg_pool.acquire() as conn:
            await conn.execute(
                "UPDATE submissions SET status='denied', deny_reason=$1 WHERE id=$2",
                self.reason.value, self.submission_id
            )
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0].copy()
            desc = embed.description or ""
            desc += f"\n❌ Card denied\nReason: {self.reason.value}"
            embed.description = desc
            embed.color = discord.Color.red()
            await interaction.message.edit(embed=embed, view=None)

        # Notify user
        view = StaffReviewView(self.bot, self.submission_id, queue_choice="normal")
        await view.notify_user("denied", reason=self.reason.value)
        await interaction.response.send_message("Card denied with reason", ephemeral=False)


class AuctionsStaff(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="auction-list", description="Liste les soumissions en attente par batch")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_list(self, interaction: discord.Interaction):
        core = self.bot.get_cog("AuctionsCore")
        async with core.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM submissions WHERE status IN ('submitted','accepted') "
                "ORDER BY batch_id NULLS LAST, id"
            )

        if not rows:
            return await interaction.response.send_message("✅ Aucune soumission en attente.", ephemeral=True)

        embed = discord.Embed(
            title="📋 Soumissions en attente",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc)
        )

        current_batch = None
        batch_lines = []
        batch_name = "Batch (no id)"
        for row in rows:
            b = row["batch_id"]
            if b != current_batch:
                if batch_lines:
                    embed.add_field(name=batch_name, value="\n".join(batch_lines), inline=False)
                    batch_lines = []
                current_batch = b
                created_dt = row["created_at"]
                batch_name = f"Batch {created_dt.strftime('%d/%m/%y')}" if created_dt else "Batch (no date)"
            batch_lines.append(
                f"#{row['id']} – <@{row['user_id']}> – {row['queue'] or 'unknown'} – {row['status']}"
            )

        if batch_lines:
            embed.add_field(name=batch_name, value="\n".join(batch_lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionsStaff(bot))
