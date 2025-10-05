import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone

from .auctions_core import GUILD_ID
from .auctions_utils import (
    next_daily_release,
    is_after_cutoff,
)

# Staff log channel
STAFF_LOG_CHANNEL_ID = 1421465080238964796  # <-- replace with your staff log channel ID


async def assign_batch(conn, queue_choice: str, now: datetime):
    """Determine the batch_id according to business rules."""
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

            now = datetime.now(timezone.utc)
            async with core.pg_pool.acquire() as conn:
                batch_id = await assign_batch(conn, self.queue_choice, now)
            release_at = next_daily_release(now)

            async with core.pg_pool.acquire() as conn:
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
            await interaction.followup.send(f"Card added to Batch {batch_id}", ephemeral=True)

            # Staff log
            log_channel = self.bot.get_channel(STAFF_LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(
                    f"✅ Submission {self.submission_id} accepted by {interaction.user.mention} "
                    f"(Batch {batch_id}, Release {release_at.strftime('%d/%m/%y %H:%M UTC')})."
                )

        except Exception as e:
            print("❌ Error in Accept:", e)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error while accepting.", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        core = self.bot.get_cog("AuctionsCore")
        if core is None or core.pg_pool is None:
            return await interaction.response.send_message("❌ Core not ready.", ephemeral=True)

        try:
            await interaction.response.defer(ephemeral=True)
            async with core.pg_pool.acquire() as conn:
                await conn.execute("UPDATE submissions SET status='denied' WHERE id=$1", self.submission_id)

            await self.update_embed(interaction, "❌ Card denied", color=discord.Color.red(), remove_view=True)
            await self.notify_user("denied")
            await interaction.followup.send("Card denied", ephemeral=True)

            # Staff log
            log_channel = self.bot.get_channel(STAFF_LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(
                    f"❌ Submission {self.submission_id} denied by {interaction.user.mention}."
                )
        except Exception as e:
            print("❌ Error in Deny:", e)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error while denying.", ephemeral=True)


class AuctionsStaff(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="auction-list", description="List accepted submissions grouped by batch")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_list(self, interaction: discord.Interaction):
        core = self.bot.get_cog("AuctionsCore")
        async with core.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT batch_id, COUNT(*) AS count FROM submissions WHERE status='accepted' GROUP BY batch_id ORDER BY batch_id"
            )
        if not rows:
            return await interaction.response.send_message("No accepted submissions.", ephemeral=True)

        embed = discord.Embed(title="📦 Pending Batches", color=discord.Color.blurple())
        for row in rows:
            embed.add_field(name=f"Batch {row['batch_id']}", value=f"{row['count']} cards", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="batch-info", description="Show details of a batch")
    @app_commands.describe(batch_id="The batch ID to inspect")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def batch_info(self, interaction: discord.Interaction, batch_id: int):
        core = self.bot.get_cog("AuctionsCore")
        if core is None or core.pg_pool is None:
            return await interaction.response.send_message("❌ Core not ready.", ephemeral=True)

        async with core.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, user_id, status, scheduled_for FROM submissions WHERE batch_id=$1 ORDER BY id",
                batch_id
            )

        if not rows:
            return await interaction.response.send_message(f"No submissions found for batch {batch_id}.", ephemeral=True)

        embed = discord.Embed(title=f"📦 Batch {batch_id}", color=discord.Color.blurple())
        for row in rows:
            try:
                user = await self.bot.fetch_user(row["user_id"])
                user_display = f"{user} ({row['user_id']})"
            except:
                user_display = f"Unknown user ({row['user_id']})"

            scheduled = row["scheduled_for"]
            scheduled_str = scheduled.strftime("%d/%m/%y %H:%M UTC") if isinstance(scheduled, datetime) else str(scheduled)

            embed.add_field(
                name=f"Submission {row['id']}",
                value=f"Author: {user_display}\nStatus: {row['status']}\nRelease: {scheduled_str}",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="batch-clear", description="Delete all submissions in a given batch")
    @app_commands.describe(batch_id="The batch ID to clear")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def batch_clear(self, interaction: discord.Interaction, batch_id: int):
        core = self.bot.get_cog("AuctionsCore")
        if core is None or core.pg_pool is None:
            return await interaction.response.send_message("❌ Core not ready.", ephemeral=True)

        try:
            await interaction.response.defer(ephemeral=False)

            async with core.pg_pool.acquire() as conn:
                count = await conn.fetchval("SELECT COUNT(*) FROM submissions WHERE batch_id=$1", batch_id)
                if count == 0:
                    return await interaction.followup.send(f"No submissions found for batch {batch_id}.", ephemeral=True)

                await conn.execute("DELETE FROM submissions WHERE batch_id=$1", batch_id)

            # Staff log
            log_channel = self.bot.get_channel(STAFF_LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(
                    f"🗑️ Batch {batch_id} cleared by {interaction.user.mention} ({count} submissions deleted)."
                )

            await interaction.followup.send(f"🗑️ Batch {batch_id} cleared ({count} submissions deleted).", ephemeral=False)
        except Exception as e:
            print("❌ Error in batch-clear:", e)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error while clearing the batch.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionsStaff(bot))
