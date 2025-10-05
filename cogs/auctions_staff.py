import json
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone

from .auctions_core import GUILD_ID
from .auctions_utils import (
    next_daily_release,
    is_after_cutoff,
)


async def assign_batch(conn, queue_choice: str, now: datetime):
    """Détermine le batch_id selon les règles."""
    if queue_choice == "normal":
        last = await conn.fetchrow("""
            SELECT batch_id, COUNT(*) AS count
            FROM submissions
            WHERE queue='normal' AND status='accepted'
            GROUP BY batch_id
            ORDER BY batch_id DESC LIMIT 1
        """)
        if not last or last["count"] >= 15:
            last_id = await conn.fetchval("SELECT COALESCE(MAX(batch_id), 0) FROM submissions")
            batch_id = (last_id or 0) + 1
        else:
            batch_id = last["batch_id"]

    elif queue_choice in ("skip", "cardmaker"):
        if is_after_cutoff(now):
            last_id = await conn.fetchval("SELECT COALESCE(MAX(batch_id), 0) FROM submissions")
            batch_id = (last_id or 0) + 1
        else:
            last_id = await conn.fetchval("SELECT COALESCE(MAX(batch_id), 0) FROM submissions")
            batch_id = last_id or 1
    else:
        batch_id = 1

    return batch_id


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
            embed.title = "✅ Votre carte a été acceptée !"
            embed.description = f"Ajoutée au Batch {batch_id}"
        else:
            embed.title = "❌ Votre carte a été refusée."
            if reason:
                embed.description = f"Raison : {reason}"
        try:
            await user.send(embed=embed)
        except:
            pass

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        core = self.bot.get_cog("AuctionsCore")
        if core is None or core.pg_pool is None:
            return await interaction.response.send_message("❌ Core not ready.", ephemeral=True)

        try:
            await interaction.response.defer(ephemeral=True)

            async with core.pg_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM submissions WHERE id=$1", self.submission_id)
            if not row:
                return await interaction.followup.send("❌ Submission not found.", ephemeral=True)

            now = datetime.now(timezone.utc)  # always aware
            async with core.pg_pool.acquire() as conn:
                batch_id = await assign_batch(conn, self.queue_choice, now)
            release_at = next_daily_release(now)  # always aware

            async with core.pg_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE submissions SET status='accepted', batch_id=$1, scheduled_for=$2 WHERE id=$3",
                    batch_id, release_at, self.submission_id
                )

            await self.update_embed(
                interaction,
                f"✅ Carte acceptée (Batch {batch_id}) • Release: {release_at.strftime('%d/%m/%y %H:%M UTC')}",
                color=discord.Color.green(),
                remove_view=True
            )
            await self.notify_user("accepted", batch_id=batch_id)
            await interaction.followup.send(f"Carte ajoutée au Batch {batch_id}", ephemeral=True)

        except Exception as e:
            print("❌ Error in Accept:", e)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Erreur lors de l'acceptation.", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        core = self.bot.get_cog("AuctionsCore")
        async with core.pg_pool.acquire() as conn:
            await conn.execute("UPDATE submissions SET status='denied' WHERE id=$1", self.submission_id)
        await self.update_embed(interaction, "❌ Carte refusée", color=discord.Color.red(), remove_view=True)
        await self.notify_user("denied")
        await interaction.response.send_message("Carte refusée", ephemeral=False)


class AuctionsStaff(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="auction-list", description="Liste les soumissions en attente par batch")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_list(self, interaction: discord.Interaction):
        core = self.bot.get_cog("AuctionsCore")
        async with core.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT batch_id, COUNT(*) AS count FROM submissions WHERE status='accepted' GROUP BY batch_id ORDER BY batch_id"
            )
        if not rows:
            return await interaction.response.send_message("Aucune soumission acceptée.", ephemeral=True)

        embed = discord.Embed(title="📦 Batches en attente", color=discord.Color.blurple())
        for row in rows:
            embed.add_field(name=f"Batch {row['batch_id']}", value=f"{row['count']} cartes", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionsStaff(bot))
