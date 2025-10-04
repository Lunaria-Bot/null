# cogs/auctions.py
import os
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional, List, Any

import discord
from discord.ext import commands
from discord import app_commands

import asyncpg
import redis.asyncio as redis

# --------- Environment loading ---------
def _get_int(name: str, default: Optional[int] = None) -> int:
    val = os.getenv(name)
    if val is None:
        if default is None:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return default
    return int(val)

GUILD_ID = _get_int("GUILD_ID")

STAFF_ROLE_ID = _get_int("STAFF_ROLE_ID", 0)  # kept for future use, no ping anymore
STAFF_ALERT_CHANNEL_ID = _get_int("STAFF_ALERT_CHANNEL_ID", 0)

QUEUE_CHANNELS = {
    "Normal Queue": _get_int("NORMAL_QUEUE_CHANNEL"),
    "Skip Queue": _get_int("SKIP_QUEUE_CHANNEL"),
    "Card Maker Queue": _get_int("CARDMAKER_QUEUE_CHANNEL"),
}

RARITY_CHANNELS = {
    "Common": _get_int("COMMON_CHANNEL"),
    "Rare": _get_int("RARE_CHANNEL"),
    "SR": _get_int("SR_CHANNEL"),
    "SSR": _get_int("SSR_CHANNEL"),
    "UR": _get_int("UR_CHANNEL"),
    "CM": _get_int("CM_CHANNEL"),
}

FEE_RECIPIENT_ID = _get_int("FEE_RECIPIENT_ID")

# PG* (Railway/Heroku) and POSTGRES_* (local) compatibility
POSTGRES_DSN = {
    "user": os.getenv("POSTGRES_USER") or os.getenv("PGUSER"),
    "password": os.getenv("POSTGRES_PASSWORD") or os.getenv("PGPASSWORD"),
    "database": os.getenv("POSTGRES_DB") or os.getenv("PGDATABASE"),
    "host": os.getenv("POSTGRES_HOST") or os.getenv("PGHOST"),
    "port": int(os.getenv("POSTGRES_PORT") or os.getenv("PGPORT", "5432")),
}

REDIS_URL = os.getenv("REDIS_URL")

DEFAULT_BATCH_SIZE = 15


class Auctions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.redis: Optional[redis.Redis] = None
        self.pg_pool: Optional[asyncpg.Pool] = None

        # Human-readable questions used across application/review/post
        self.questions: List[str] = [
            "Card details",
            "Currency preference(s)",
            "Rate (BS:MS)",
            "Screenshot",
            "Free skip used",
            "Fees confirmation",
        ]

    # ---------- Lifecycle ----------
    async def cog_load(self):
        if REDIS_URL:
            self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self.pg_pool = await asyncpg.create_pool(**POSTGRES_DSN)

        async with self.pg_pool.acquire() as conn:
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                queue TEXT NOT NULL,
                answers JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'submitted',  -- submitted | approved | denied | released
                batch_id INT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS batches (
                id SERIAL PRIMARY KEY,
                queue TEXT NOT NULL,
                scheduled_for TIMESTAMP NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """)

    async def cog_unload(self):
        if self.redis:
            await self.redis.close()
        if self.pg_pool:
            await self.pg_pool.close()

    # ---------- Helpers ----------
    def _rarity_from_details(self, details: str) -> Optional[str]:
        text = (details or "").lower()

        def has_token(tok: str):
            return f" {tok} " in f" {text} " or text.startswith(f"{tok} ") or text.endswith(f" {tok}")

        if has_token("ur"):
            return "UR"
        if has_token("ssr"):
            return "SSR"
        if has_token("sr"):
            return "SR"
        if has_token("cm") or "card maker" in text:
            return "CM"
        if has_token("rare"):
            return "Rare"
        if has_token("common"):
            return "Common"
        return None

    async def _dm_user(self, guild: Optional[discord.Guild], user_id: int, message: str):
        if not guild:
            return
        member = guild.get_member(user_id)
        if not member:
            return
        try:
            dm = await member.create_dm()
            await dm.send(message)
        except discord.Forbidden:
            pass

    def _ensure_list_answers(self, answers: Any) -> List[str]:
        if isinstance(answers, list):
            return [str(a) if not isinstance(a, str) else a for a in answers]
        if isinstance(answers, str):
            try:
                parsed = json.loads(answers)
                if isinstance(parsed, list):
                    return [str(a) if not isinstance(a, str) else a for a in parsed]
                return [answers]
            except Exception:
                return [answers]
        return [str(answers)]

    def _apply_answers_to_embed(self, embed: discord.Embed, answers: List[str]):
        """
        Add human-readable question labels to the embed and show the screenshot inline if possible.
        Keeps under Discord's 25-field limit by capping answer fields.
        """
        # Reserve 3 fields at top (User/Queue/Status etc.) elsewhere -> keep answers <= 20
        max_answer_fields = 20

        for i, ans in enumerate(answers[:len(self.questions)]):
            label = self.questions[i]
            if i == 3 and ans and str(ans).startswith("http"):
                # Question 4 is screenshot -> show image
                try:
                    embed.set_image(url=str(ans))
                except Exception:
                    embed.add_field(name=label, value=(ans or "—"), inline=False)
            else:
                embed.add_field(name=label, value=(ans or "—"), inline=False)

            if len(embed.fields) >= max_answer_fields:
                break

        extra = len(answers) - len(self.questions)
        if extra > 0:
            embed.add_field(
                name="…",
                value=f"{extra} additional answers not displayed",
                inline=False
            )

    # ---------- Application ----------
    @app_commands.command(name="auction", description="Apply for an auction")
    @app_commands.choices(queue=[
        app_commands.Choice(name="Skip Queue", value="Skip Queue"),
        app_commands.Choice(name="Normal Queue", value="Normal Queue"),
        app_commands.Choice(name="Card Maker Queue", value="Card Maker Queue"),
    ])
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_apply(self, interaction: discord.Interaction, queue: app_commands.Choice[str]):
        try:
            dm = await interaction.user.create_dm()
        except discord.Forbidden:
            await interaction.response.send_message("❌ I cannot DM you. Please enable DMs.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Lilac Auction Submission",
            description=(
                "Are you sure you want to apply?\n\n"
                "Once you start the application I will send you a series of questions. "
                "You will have 60 minutes to complete the application."
            ),
            color=discord.Color.purple()
        )

        view = self.ApplicationStartView(self, interaction.user, queue.value)
        await dm.send(embed=embed, view=view)
        await interaction.response.send_message("📩 Check your DMs to continue your application.", ephemeral=True)

    class ApplicationStartView(discord.ui.View):
        def __init__(self, cog: "Auctions", user: discord.User, queue: str):
            super().__init__(timeout=300)
            self.cog = cog
            self.user = user
            self.queue = queue

        @discord.ui.button(label="Start Application", style=discord.ButtonStyle.green)
        async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.user.id:
                await interaction.response.send_message("Not your application.", ephemeral=True)
                return
            await interaction.response.defer()
            await self.cog.run_application(self.user, self.queue)
            self.stop()

        @discord.ui.button(label="Cancel Application", style=discord.ButtonStyle.red)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.user.id:
                await interaction.response.send_message("Not your application.", ephemeral=True)
                return
            await interaction.response.send_message("❌ Application cancelled.", ephemeral=True)
            self.stop()

    async def run_application(self, user: discord.User, queue: str):
        guild = self.bot.get_guild(GUILD_ID)
        dm = await user.create_dm()

        # Ask using the human-readable labels
        questions_text = [
            "1/6. Card details",
            "2/6. Currency preference(s)",
            "3/6. Rate (BS:MS)",
            "4/6. Screenshot (URL or attach an image)",
            "5/6. Free skip used",
            f"6/6. Fees confirmation (send fees to <@{FEE_RECIPIENT_ID}> then type 'sent')",
        ]

        answers: List[str] = []

        def check(m: discord.Message):
            return m.author.id == user.id and m.channel.id == dm.id

        for q in questions_text:
            await dm.send(embed=discord.Embed(title="Lilac Auction Submission", description=q, color=discord.Color.purple()))
            try:
                msg = await self.bot.wait_for("message", check=check, timeout=3600)
            except asyncio.TimeoutError:
                await dm.send("⏰ Application timed out. Please restart.")
                return
            content = msg.content.strip()
            if not content and msg.attachments:
                content = msg.attachments[0].url
            answers.append(content)

        rarity = self._rarity_from_details(answers[0] if answers else "")
        if not rarity:
            async with self.pg_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO submissions(user_id, queue, answers, status, created_at) VALUES($1,$2,$3,'denied', NOW())",
                    user.id, queue, json.dumps(answers)
                )
            await dm.send("❌ Your application got rejected! Reason: Rarity not detected.")
            return

        async with self.pg_pool.acquire() as conn:
            submission_id = await conn.fetchval(
                "INSERT INTO submissions(user_id, queue, answers, status, created_at) VALUES($1,$2,$3,'submitted', NOW()) RETURNING id",
                user.id, queue, json.dumps(answers)
            )

        await dm.send(f"✅ Your application has been submitted successfully! (ID: {submission_id})")

        # Post summary in queue channel (no staff ping anymore)
        if guild:
            queue_channel_id = QUEUE_CHANNELS.get(queue)
            channel = guild.get_channel(queue_channel_id) if queue_channel_id else None
            if channel:
                embed = discord.Embed(
                    title=f"Lilac Auction Submission #{submission_id}",
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="Queue", value=queue, inline=True)
                embed.add_field(name="Submitted by", value=user.mention, inline=True)
                # Show answers using readable labels, include image
                self._apply_answers_to_embed(embed, answers)
                await channel.send(embed=embed)

        # Inform staff channel (no role mention)
        if guild and STAFF_ALERT_CHANNEL_ID:
            alert_channel = guild.get_channel(STAFF_ALERT_CHANNEL_ID)
            if alert_channel:
                await alert_channel.send(f"New auction submission #{submission_id} in {queue}. Use `/auction-review {submission_id}` to review.")

    # ---------- Review ----------
    @app_commands.command(name="auction-review", description="Review a specific auction submission")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_review(self, interaction: discord.Interaction, submission_id: int):
        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM submissions WHERE id=$1", submission_id)
        if not row:
            await interaction.response.send_message("❌ Submission not found.", ephemeral=True)
            return

        answers = self._ensure_list_answers(row["answers"])
        user_id = row["user_id"]
        queue = row["queue"]
        status = row["status"]

        embed = discord.Embed(
            title=f"Auction Submission #{submission_id}",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="User", value=f"<@{user_id}>", inline=True)
        embed.add_field(name="Queue", value=queue, inline=True)
        embed.add_field(name="Status", value=status, inline=True)

        # Add readable Q labels and image inline
        self._apply_answers_to_embed(embed, answers)

        view = self.ReviewView(self, submission_id, user_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    class DenyReasonModal(discord.ui.Modal, title="Deny Submission"):
        reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, required=True)

        def __init__(self, cog: "Auctions", submission_id: int, user_id: int):
            super().__init__()
            self.cog = cog
            self.submission_id = submission_id
            self.user_id = user_id

        async def on_submit(self, interaction: discord.Interaction):
            async with self.cog.pg_pool.acquire() as conn:
                await conn.execute("UPDATE submissions SET status='denied' WHERE id=$1", self.submission_id)
            await interaction.response.send_message(
                f"❌ Submission #{self.submission_id} denied.\nReason: {self.reason.value}",
                ephemeral=True
            )
            await self.cog._dm_user(interaction.guild, self.user_id,
                                    f"❌ Your auction submission (ID: {self.submission_id}) was denied.\nReason: {self.reason.value}")

    class ReviewView(discord.ui.View):
        def __init__(self, cog: "Auctions", submission_id: int, user_id: int):
            super().__init__(timeout=600)
            self.cog = cog
            self.submission_id = submission_id
            self.user_id = user_id

        @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
        async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
            async with self.cog.pg_pool.acquire() as conn:
                await conn.execute("UPDATE submissions SET status='approved' WHERE id=$1", self.submission_id)
            await interaction.response.send_message(
                f"✅ Approved submission #{self.submission_id}. Assign it to a batch with `/batch-build` or `/batch-assign`.",
                ephemeral=True
            )
            await self.cog._dm_user(interaction.guild, self.user_id,
                                    f"✅ Your auction submission (ID: {self.submission_id}) has been approved.")

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.gray)
        async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Keep simple deny without reason if needed
            async with self.cog.pg_pool.acquire() as conn:
                await conn.execute("UPDATE submissions SET status='denied' WHERE id=$1", self.submission_id)
            await interaction.response.send_message(f"❌ Submission #{self.submission_id} denied.", ephemeral=True)
            await self.cog._dm_user(interaction.guild, self.user_id,
                                    f"❌ Your auction submission (ID: {self.submission_id}) has been declined.")

        @discord.ui.button(label="Deny with reason", style=discord.ButtonStyle.red)
        async def deny_with_reason(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_modal(
                Auctions.DenyReasonModal(self.cog, self.submission_id, self.user_id)
            )

    # ---------- Batch building ----------
    @app_commands.command(name="batch-build", description="Build batches from approved submissions")
    @app_commands.describe(queue="Which queue to batch", size="Cards per batch (default 15)", scheduled_for="ISO date-time for publication (optional)")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def batch_build(self, interaction: discord.Interaction, queue: str, size: Optional[int] = DEFAULT_BATCH_SIZE, scheduled_for: Optional[str] = None):
        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id FROM submissions
                WHERE queue=$1 AND status='approved' AND batch_id IS NULL
                ORDER BY id ASC
            """, queue)

        if not rows:
            await interaction.response.send_message("📭 No approved submissions to batch.", ephemeral=True)
            return

        ids = [r["id"] for r in rows]
        batches_created = 0
        scheduled_dt = None
        if scheduled_for:
            try:
                scheduled_dt = datetime.fromisoformat(scheduled_for)
            except Exception:
                await interaction.response.send_message("❌ Invalid scheduled_for format. Use ISO 8601 (e.g., 2025-10-02T18:00:00).", ephemeral=True)
                return

        async with self.pg_pool.acquire() as conn:
            for i in range(0, len(ids), size):
                chunk = ids[i:i+size]
                batch_id = await conn.fetchval(
                    "INSERT INTO batches(queue, scheduled_for) VALUES($1, $2) RETURNING id",
                    queue, scheduled_dt
                )
                for sid in chunk:
                    await conn.execute("UPDATE submissions SET batch_id=$1 WHERE id=$2", batch_id, sid)
                batches_created += 1

        await interaction.response.send_message(f"✅ Built {batches_created} batch(es) for {queue}.", ephemeral=True)

    @app_commands.command(name="batch-assign", description="Assign a submission to a specific batch")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def batch_assign(self, interaction: discord.Interaction, submission_id: int, batch_id: int):
        async with self.pg_pool.acquire() as conn:
            exists = await conn.fetchval("SELECT 1 FROM batches WHERE id=$1", batch_id)
            if not exists:
                await interaction.response.send_message("❌ Batch not found.", ephemeral=True)
                return
            await conn.execute("UPDATE submissions SET batch_id=$1 WHERE id=$2", batch_id, submission_id)
        await interaction.response.send_message(f"✅ Assigned submission #{submission_id} to batch {batch_id}.", ephemeral=True)

    # ---------- Post batch ----------
    @app_commands.command(name="post-auctions", description="Post all auctions in a batch to rarity channels")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def post_auctions(self, interaction: discord.Interaction, batch_id: int):
        guild = interaction.guild

        async with self.pg_pool.acquire() as conn:
            batch = await conn.fetchrow("SELECT * FROM batches WHERE id=$1", batch_id)
            if not batch:
                await interaction.response.send_message("❌ Batch not found.", ephemeral=True)
                return
            subs = await conn.fetch("""
                SELECT * FROM submissions
                WHERE batch_id=$1 AND status='approved'
                ORDER BY id ASC
            """, batch_id)

        if not subs:
            await interaction.response.send_message("📭 No approved submissions in this batch.", ephemeral=True)
            return

        posted_count = 0
        for row in subs:
            answers = self._ensure_list_answers(row["answers"])
            user_id = row["user_id"]
            queue = row["queue"]

            rarity = self._rarity_from_details(answers[0] if answers else "")
            if not rarity:
                async with self.pg_pool.acquire() as conn:
                    await conn.execute("UPDATE submissions SET status='denied' WHERE id=$1", row["id"])
                await self._dm_user(guild, user_id, f"❌ Your auction submission (ID: {row['id']}) was rejected at posting stage. Reason: Rarity not detected.")
                continue

            post_channel_id = RARITY_CHANNELS.get(rarity) or QUEUE_CHANNELS.get(queue)
            channel = guild.get_channel(post_channel_id) if guild else None
            if not channel:
                continue

            seller = guild.get_member(user_id) if guild else None

            embed = discord.Embed(
                title=f"Auction: {rarity}",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            # Show readable answers and inline image
            self._apply_answers_to_embed(embed, answers)
            embed.add_field(name="Seller", value=(seller.mention if seller else f"<@{user_id}>"), inline=True)
            embed.add_field(name="Queue", value=queue, inline=True)

            # Safety: stay well under 25 fields
            if len(embed.fields) > 23:
                # Compact fallback (should rarely happen)
                embed.clear_fields()
                # Keep only essential info
                embed.add_field(name="Seller", value=(seller.mention if seller else f"<@{user_id}>"), inline=True)
                embed.add_field(name="Queue", value=queue, inline=True)

            await channel.send(embed=embed)
            posted_count += 1

            async with self.pg_pool.acquire() as conn:
                await conn.execute("UPDATE submissions SET status='released' WHERE id=$1", row["id"])

        await interaction.response.send_message(f"📢 Posted {posted_count} auction(s) from batch {batch_id}.", ephemeral=True)

    # ---------- Utility: list submissions ----------
    @app_commands.command(name="auction-list", description="List submissions by status and/or queue")
    @app_commands.describe(status="submitted/approved/denied/released", queue="Queue filter")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_list(self, interaction: discord.Interaction, status: Optional[str] = None, queue: Optional[str] = None):
        query = "SELECT id, user_id, queue, status FROM submissions WHERE 1=1"
        params: List[Any] = []
        if status:
            params.append(status)
            query += f" AND status=${len(params)}"
        if queue:
            params.append(queue)
            query += f" AND queue=${len(params)}"
        query += " ORDER BY id DESC LIMIT 50"

        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        if not rows:
            await interaction.response.send_message("📭 No submissions match filters.", ephemeral=True)
            return

        lines = [f"#{r['id']} • {r['queue']} • {r['status']} • <@{r['user_id']}>" for r in rows]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Auctions(bot))
