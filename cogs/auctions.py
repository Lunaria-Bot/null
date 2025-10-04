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

# --------- Load from environment ---------
GUILD_ID = int(os.getenv("GUILD_ID"))

STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID"))
STAFF_ALERT_CHANNEL_ID = int(os.getenv("STAFF_ALERT_CHANNEL_ID"))

QUEUE_CHANNELS = {
    "Normal Queue": int(os.getenv("NORMAL_QUEUE_CHANNEL")),
    "Skip Queue": int(os.getenv("SKIP_QUEUE_CHANNEL")),
    "Card Maker Queue": int(os.getenv("CARDMAKER_QUEUE_CHANNEL")),
}

RARITY_CHANNELS = {
    "Common": int(os.getenv("COMMON_CHANNEL")),
    "Rare": int(os.getenv("RARE_CHANNEL")),
    "SR": int(os.getenv("SR_CHANNEL")),
    "SSR": int(os.getenv("SSR_CHANNEL")),
    "UR": int(os.getenv("UR_CHANNEL")),
    "CM": int(os.getenv("CM_CHANNEL")),
}

FEE_RECIPIENT_ID = int(os.getenv("FEE_RECIPIENT_ID"))

# Compat PG* (Railway/Heroku) et POSTGRES_* (local)
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

    async def _dm_user(self, guild: discord.Guild, user_id: int, message: str):
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
            return answers
        if isinstance(answers, str):
            try:
                parsed = json.loads(answers)
                if isinstance(parsed, list):
                    return parsed
                return [answers]
            except Exception:
                return [answers]
        return [str(answers)]

    # ---------- Application entry ----------
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
                "You will have 60 minutes to complete the application. If you do not "
                "complete the application in time, you will have to restart. If you wish "
                "to stop the application feel free to click the cancel button at any time."
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

        questions = [
            "1/6. Please enter the details of the card. ONE card at a time only. If it’s an event card, please mention the event (e.g. SR Emilia V1 Halloween/SR Emilia V1 Christmas).",
            "2/6. Please insert your currency preference(s). (example: Bloodstones / Moonstones)",
            "3/6. If accepting Bloodstones and Moonstones, please enter your Bloodstones to Moonstones rate. (example: 275:1)",
            "4/6. Please add the screenshot of the card. Make sure the version number is clearly seen.",
            "5/6. If you're a server booster or clan member, are you using your free queue skip? (Boosters and clan members can use a free queue skip once every 2 weeks, just say no if you're not)",
            f"6/6. Send the auction fees to <@{FEE_RECIPIENT_ID}> by using the Mazoku command, /trade create.\nType 'sent' once done."
        ]

        answers: List[str] = []

        def check(m: discord.Message):
            return m.author.id == user.id and m.channel.id == dm.id

        for q in questions:
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

        # ---------- Post summary in queue channel + ping staff ----------
        queue_channel_id = QUEUE_CHANNELS.get(queue)
        if queue_channel_id and guild:
            channel = guild.get_channel(queue_channel_id)
            if channel:
                embed = discord.Embed(
                    title=f"Lilac Auction Submission #{submission_id}",
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="Queue", value=queue, inline=True)
                embed.add_field(name="Submitted by", value=user.mention, inline=True)
                # Ajoute jusqu'à 20 Qx pour rester sous la limite
                for i, ans in enumerate(answers[:20], start=1):
                    embed.add_field(name=f"Q{i}", value=(ans or "—"), inline=False)
                if len(answers) > 20:
                    embed.add_field(name="…", value=f"{len(answers) - 20} more answers not shown", inline=False)
                await channel.send(embed=embed)

        if guild:
            staff_role = guild.get_role(STAFF_ROLE_ID)
            alert_channel = guild.get_channel(STAFF_ALERT_CHANNEL_ID)
            if staff_role and alert_channel:
                await alert_channel.send(f"📢 {staff_role.mention} New submission #{submission_id} in {queue}. Use `/auction-review {submission_id}` to review.")

    # ---------- Review command ----------
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

        # Limite à 20 champs pour rester < 25 au total
        for i, ans in enumerate(answers[:20], start=1):
            embed.add_field(name=f"Q{i}", value=(ans or "—"), inline=False)
        if len(answers) > 20:
            embed.add_field(
                name="…",
                value=f"{len(answers) - 20} additional answers not displayed",
                inline=False
            )

        view = self.ReviewView(self, submission_id, user_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

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
            await interaction.response.send_message(f"✅ Approved submission #{self.submission_id}. Assign it to a batch with `/batch-build` or `/batch-assign`.", ephemeral=True)
            await self.cog._dm_user(interaction.guild, self.user_id, f"✅ Your auction submission (ID: {self.submission_id}) has been approved.")

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
        async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
            async with self.cog.pg_pool.acquire() as conn:
                await conn.execute("UPDATE submissions SET status='denied' WHERE id=$1", self.submission_id)
            await interaction.response.send_message(f"❌ Denied submission #{self.submission_id}.", ephemeral=True)
            await self.cog._dm_user(interaction.guild, self.user_id, f"❌ Your auction submission (ID: {self.submission_id}) has been declined.")

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
            desc_lines = [
                f"• Card details: {answers[0] if len(answers) > 0 else '—'}",
                f"• Currency preference(s): {answers[1] if len(answers) > 1 else '—'}",
                f"• Rate (BS:MS): {answers[2] if len(answers) > 2 else '—'}",
                f"• Screenshot: {answers[3] if len(answers) > 3 else '—'}",
                f"• Free skip used: {answers[4] if len(answers) > 4 else '—'}",
                f"• Fees: {answers[5] if len(answers) > 5 else '—'}",
            ]
            embed = discord.Embed(
                title=f"Auction: {rarity}",
                description="\n".join(desc_lines),
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            if answers and len(answers) > 3 and str(answers[3]).startswith("http"):
                embed.set_image(url=str(answers[3]))
            embed.add_field(name="Seller", value=(seller.mention if seller else f"<@{user_id}>"), inline=True)
            embed.add_field(name="Queue", value=queue, inline=True)

            # Sécurité: ne jamais dépasser 25 champs
            if len(embed.fields) > 20:
                # En cas d'ajout imprévu, on résume
                embed.clear_fields()
                embed.add_field(name="Info", value="Too many fields, compact summary shown.", inline=False)
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
