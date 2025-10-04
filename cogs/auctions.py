import os
import json
import re
from datetime import datetime, timedelta, timezone
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncpg
import redis.asyncio as redis

# ---------- Safe environment loader ----------
def get_int_env(name: str, required: bool = True, default: int = 0) -> int:
    val = os.getenv(name)
    if val is None:
        if required:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return default
    try:
        return int(val)
    except ValueError:
        raise RuntimeError(f"Environment variable {name} must be an integer, got: {val}")

# --- Required IDs ---
GUILD_ID = get_int_env("GUILD_ID", required=True)

# --- Mazoku IDs and channel ---
MAZOKU_BOT_ID = 1242388858897956906
MAZOKU_CHANNEL_ID = 1303054862447022151

# --- Rarity → channel mapping ---
RARITY_CHANNELS = {
    1342202221558763571: 1304507540645740666,  # Common
    1342202219574857788: 1304507516423766098,  # Rare
    1342202597389373530: 1304536219677626442,  # SR
    1342202212948115510: 1304502617472503908,  # SSR
    1342202203515125801: 1304052056109350922,  # UR
}
CARDMAKER_CHANNEL_ID = 1395405043431116871

# --- Release scheduling (daily at specific time) ---
RELEASE_HOUR_UTC = 21   # from <t:1759593420:t> → 21:57 UTC
RELEASE_MINUTE_UTC = 57

# --- Auction release channel (where new auctions are posted at release time) ---
AUCTION_CHANNEL_ID = get_int_env("AUCTION_CHANNEL_ID", required=False, default=0)

# --- Database / Redis ---
POSTGRES_DSN = {
    "user": os.getenv("POSTGRES_USER") or os.getenv("PGUSER"),
    "password": os.getenv("POSTGRES_PASSWORD") or os.getenv("PGPASSWORD"),
    "database": os.getenv("POSTGRES_DB") or os.getenv("PGDATABASE"),
    "host": os.getenv("POSTGRES_HOST") or os.getenv("PGHOST"),
    "port": int(os.getenv("POSTGRES_PORT") or os.getenv("PGPORT", "5432")),
}
REDIS_URL = os.getenv("REDIS_URL")

# --- Utility: strip all Discord custom emojis from text ---
EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
def strip_discord_emojis(text: str) -> str:
    return EMOJI_RE.sub("", text).strip()

# --- Utility: next daily release datetime (UTC) ---
def next_daily_release(now_utc: datetime) -> datetime:
    target = now_utc.replace(hour=RELEASE_HOUR_UTC, minute=RELEASE_MINUTE_UTC, second=0, microsecond=0)
    if target <= now_utc:
        target += timedelta(days=1)
    return target


class Auctions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.redis: redis.Redis = None
        self.pg_pool: asyncpg.Pool = None
        self.last_release_marker_key = "auctions:last_release_date"
        self.check_auctions.start()

    async def cog_load(self):
        if not REDIS_URL:
            raise RuntimeError("REDIS_URL is not set")
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self.pg_pool = await asyncpg.create_pool(**POSTGRES_DSN)

        # Ensure schema: add technical columns to track messages and closing state
        async with self.pg_pool.acquire() as conn:
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                card JSONB NOT NULL,
                currency TEXT,
                rate TEXT,
                queue TEXT,
                batch_id INT,
                fees_paid BOOLEAN,
                deny_reason TEXT,
                status TEXT NOT NULL DEFAULT 'submitted',
                scheduled_for TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """)
            # Add tracking columns if not exist
            await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS queue_message_id BIGINT")
            await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS queue_channel_id BIGINT")
            await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS queue_thread_id BIGINT")
            await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS released_message_id BIGINT")
            await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS released_channel_id BIGINT")
            await conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS closed BOOLEAN DEFAULT FALSE")

    async def cog_unload(self):
        if self.redis:
            await self.redis.close()
        if self.pg_pool:
            await self.pg_pool.close()
        self.check_auctions.cancel()

    # --- Capture Mazoku card embeds and cache by owner ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            message.author.bot
            and message.author.id == MAZOKU_BOT_ID
            and message.channel.id == MAZOKU_CHANNEL_ID
        ):
            await self._process_mazoku_embed(message)

    @commands.Cog.listener())
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if (
            after.author.bot
            and after.author.id == MAZOKU_BOT_ID
            and after.channel.id == MAZOKU_CHANNEL_ID
        ):
            await self._process_mazoku_embed(after)

    async def _process_mazoku_embed(self, message: discord.Message):
        if not message.embeds:
            return
        embed = message.embeds[0]
        data = embed.to_dict()
        desc = data.get("description", "") or ""
        if "Owned by <@" in desc:
            try:
                start = desc.find("Owned by <@") + len("Owned by <@")
                end = desc.find(">", start)
                owner_id = int(desc[start:end])
                await self.redis.set(f"mazoku:card:{owner_id}", json.dumps(data), ex=600)
            except Exception as e:
                print("❗ Error parsing owner_id:", e)

    # --- Slash command: auction-submit ---
    @app_commands.command(name="auction-submit", description="Submit your Mazoku card for auction")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_submit(self, interaction: discord.Interaction):
        data = await self.redis.get(f"mazoku:card:{interaction.user.id}")
        if not data:
            return await interaction.response.send_message(
                "❌ No Mazoku card detected. Use `/inventory` with Mazoku first.",
                ephemeral=True
            )
        card_embed = discord.Embed.from_dict(json.loads(data))
        if card_embed.title:
            card_embed.title = strip_discord_emojis(card_embed.title)

        dm = await interaction.user.create_dm()
        # Simple view: we assume rate/currency/queue are set elsewhere or defaults; you can extend with modals if needed
        await dm.send(
            "Here is your submitted card",
            embed=card_embed,
            view=self.AuctionSetupView(self, interaction.user, card_embed)
        )
        await interaction.response.send_message("📩 Check your DMs to finish your auction submission.", ephemeral=True)

    # --- DM Form (simplifié) ---
    class AuctionSetupView(discord.ui.View):
        def __init__(self, cog: "Auctions", user: discord.User, card_embed: discord.Embed):
            super().__init__(timeout=600)
            self.cog = cog
            self.user = user
            self.card_embed = card_embed
            self.currency = "Coins"
            self.rate = "1:1"
            self.queue_choice = "normal"  # default; change as needed

        @discord.ui.select(
            placeholder="Choose a queue",
            options=[
                discord.SelectOption(label="Card Maker Queue", value="cardmaker"),
                discord.SelectOption(label="Normal Queue", value="normal"),
                discord.SelectOption(label="Skip Queue", value="skip"),
            ]
        )
        async def select_queue(self, interaction, select):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            self.queue_choice = select.values[0]
            await interaction.response.edit_message(view=self)

        @discord.ui.button(label="Submit Auction", style=discord.ButtonStyle.green)
        async def submit(self, interaction, button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)

            # Insert into DB
            async with self.cog.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "INSERT INTO submissions(user_id, card, currency, rate, queue, status) "
                    "VALUES($1,$2::jsonb,$3,$4,$5,'submitted') RETURNING id",
                    self.user.id,
                    json.dumps(self.card_embed.to_dict()),
                    self.currency,
                    self.rate,
                    self.queue_choice
                )
                submission_id = row["id"]

            # Determine target channel by rarity or cardmaker queue
            if self.queue_choice == "cardmaker":
                channel_id = CARDMAKER_CHANNEL_ID
            else:
                match = re.search(r"<a?:\w+:(\d+)>", self.card_embed.description or "")
                rarity_id = int(match.group(1)) if match else None
                channel_id = RARITY_CHANNELS.get(rarity_id)

            channel = self.cog.bot.get_channel(channel_id) if channel_id else None
            if not channel:
                return await interaction.response.send_message("❌ Salon introuvable (rareté inconnue).", ephemeral=True)

            # Post to queue and create thread
            msg = await channel.send(
                embed=self.card_embed,
                view=self.cog.StaffReviewView(self.cog, submission_id, self.queue_choice)
            )
            thread = await msg.create_thread(name=f"Auction #{submission_id} – {self.card_embed.title or 'Card'}")

            # Save message/thread refs
            async with self.cog.pg_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE submissions SET queue_message_id=$1, queue_channel_id=$2, queue_thread_id=$3 WHERE id=$4",
                    msg.id, channel.id, thread.id, submission_id
                )

            await interaction.response.send_message("✅ Soumission envoyée.", ephemeral=True)
            self.stop()

    # --- Staff review view with embed updates + auto DM to user ---
    class StaffReviewView(discord.ui.View):
        def __init__(self, cog: "Auctions", submission_id: int, queue_choice: str):
            super().__init__(timeout=None)
            self.cog = cog
            self.submission_id = submission_id
            self.queue_choice = queue_choice

        async def update_embed(self, interaction: discord.Interaction, extra_text: str,
                               color: discord.Color = None, remove_view: bool = False):
            if not interaction.message.embeds:
                return
            embed = interaction.message.embeds[0].copy()
            desc = embed.description or ""
            desc += f"\n{extra_text}"
            embed.description = desc
            if color:
                embed.color = color
            await interaction.message.edit(embed=embed, view=None if remove_view else self)

        async def notify_user(self, status: str, reason: str = None, batch_id: int = None):
            async with self.cog.pg_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT user_id FROM submissions WHERE id=$1", self.submission_id)
            if not row:
                return
            user = await self.cog.bot.fetch_user(row["user_id"])
            embed = discord.Embed(color=discord.Color.green() if status == "accepted" else discord.Color.red())
            if status == "accepted":
                embed.title = "✅ Your card has been accepted!"
                embed.description = f"Added to Batch {batch_id}"
            elif status == "denied":
                embed.title = "❌ Your card has been denied."
                if reason:
                    embed.description = f"Reason: {reason}"
            try:
                await user.send(embed=embed)
            except:
                pass

        @discord.ui.button(label="Fees Paid", style=discord.ButtonStyle.green)
        async def fees_paid_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            async with self.cog.pg_pool.acquire() as conn:
                await conn.execute("UPDATE submissions SET fees_paid = TRUE WHERE id=$1", self.submission_id)
            await self.update_embed(interaction, "Fee paid 💵")
            await interaction.response.send_message("✅ Fees marked as paid", ephemeral=True)

        @discord.ui.button(label="Fees Not Paid", style=discord.ButtonStyle.red)
        async def fees_not_paid_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            async with self.cog.pg_pool.acquire() as conn:
                await conn.execute("UPDATE submissions SET fees_paid = FALSE WHERE id=$1", self.submission_id)
            await self.update_embed(interaction, "❌ Fees not paid")
            await interaction.response.send_message("❌ Fees marked as not paid", ephemeral=True)

        @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
        async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            batch_id, scheduled_for = await self.cog.add_to_batch(self.submission_id, self.queue_choice)
            await self.update_embed(
                interaction,
                f"✅ Card accepted (Batch {batch_id}) • Release: {scheduled_for.strftime('%d/%m/%y %H:%M UTC')}",
                color=discord.Color.green(),
                remove_view=True
            )
            await self.notify_user("accepted", batch_id=batch_id)
            await interaction.response.send_message(f"Card added to Batch {batch_id}", ephemeral=False)

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
        async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            async with self.cog.pg_pool.acquire() as conn:
                await conn.execute("UPDATE submissions SET status='denied' WHERE id=$1", self.submission_id)
            await self.update_embed(interaction, "❌ Card denied", color=discord.Color.red(), remove_view=True)
            await self.notify_user("denied")
            await interaction.response.send_message("Card denied", ephemeral=False)

        @discord.ui.button(label="Deny with reason", style=discord.ButtonStyle.gray)
        async def deny_reason_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_modal(self.cog.DenyReasonModal(self.cog, self.submission_id))

    class DenyReasonModal(discord.ui.Modal, title="Deny with reason"):
        reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, required=True)

        def __init__(self, cog: "Auctions", submission_id: int):
            super().__init__()
            self.cog = cog
            self.submission_id = submission_id

        async def on_submit(self, interaction: discord.Interaction):
            async with self.cog.pg_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE submissions SET status='denied', deny_reason=$1 WHERE id=$2",
                    self.reason.value, self.submission_id
                )
            # Update embed and remove buttons
            if interaction.message and interaction.message.embeds:
                embed = interaction.message.embeds[0].copy()
                desc = embed.description or ""
                desc += f"\n❌ Card denied\nReason: {self.reason.value}"
                embed.description = desc
                embed.color = discord.Color.red()
                await interaction.message.edit(embed=embed, view=None)
            await self.cog.StaffReviewView.notify_user(self, "denied", reason=self.reason.value)
            await interaction.response.send_message("Card denied with reason", ephemeral=False)

    # --- Batch management (assign batch and schedule for next daily release) ---
    async def add_to_batch(self, submission_id: int, queue_choice: str):
        now_utc = datetime.now(timezone.utc)
        release_at = next_daily_release(now_utc)

        async with self.pg_pool.acquire() as conn:
            if queue_choice == "normal":
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
                # For skip and cardmaker, use batch 1 (or customize)
                batch_id = 1

            await conn.execute(
                "UPDATE submissions SET status='accepted', batch_id=$1, scheduled_for=$2 WHERE id=$3",
                batch_id, release_at, submission_id
            )
            return batch_id, release_at

    # --- Staff list: show pending per batch, named "Batch DD/MM/YY" ---
    @app_commands.command(name="auction-list", description="Liste les soumissions en attente par batch")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_list(self, interaction: discord.Interaction):
        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM submissions WHERE status IN ('submitted','accepted') ORDER BY batch_id NULLS LAST, id"
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
                # Name by created_at of first item in batch, or today's date if null
                created_dt = row["created_at"] or datetime.now(timezone.utc)
                batch_name = f"Batch {created_dt.strftime('%d/%m/%y')}"
            batch_lines.append(
                f"#{row['id']} – <@{row['user_id']}> – {row['queue'] or 'unknown'} – {row['status']}"
            )

        if batch_lines:
            embed.add_field(name=batch_name, value="\n".join(batch_lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- Scheduler: daily release at configured time ---
    @tasks.loop(minutes=1)
    async def check_auctions(self):
        now_utc = datetime.now(timezone.utc)
        target = now_utc.replace(hour=RELEASE_HOUR_UTC, minute=RELEASE_MINUTE_UTC, second=0, microsecond=0)
        today_key = now_utc.strftime("%Y-%m-%d")

        # Ensure we run once per day at the target minute
        last_run_day = await self.redis.get(self.last_release_marker_key)
        if now_utc >= target and (last_run_day != today_key):
            # 1) Close previous released auctions (if not closed yet)
            async with self.pg_pool.acquire() as conn:
                prev_rows = await conn.fetch(
                    "SELECT id, queue_channel_id, queue_message_id, closed FROM submissions WHERE status='released' AND closed=FALSE"
                )
                for r in prev_rows:
                    ch = self.bot.get_channel(r["queue_channel_id"]) if r["queue_channel_id"] else None
                    if ch:
                        try:
                            msg = await ch.fetch_message(r["queue_message_id"])
                            if msg and msg.embeds:
                                emb = msg.embeds[0].copy()
                                desc = emb.description or ""
                                desc += "\nAuction closed ❌"
                                emb.description = desc
                                await msg.edit(embed=emb, view=None)
                        except Exception as e:
                            print("❗ close previous auctions error:", e)
                    await conn.execute("UPDATE submissions SET closed=TRUE WHERE id=$1", r["id"])

                # 2) Post new releases (accepted and scheduled_for <= now)
                new_rows = await conn.fetch(
                    "SELECT id, card FROM submissions WHERE status='accepted' AND scheduled_for <= $1",
                    now_utc
                )
                release_channel = self.bot.get_channel(AUCTION_CHANNEL_ID) if AUCTION_CHANNEL_ID else None
                for r in new_rows:
                    try:
                        card_dict = r["card"] if isinstance(r["card"], dict) else json.loads(r["card"])
                        card_embed = discord.Embed.from_dict(card_dict)
                    except Exception:
                        card_embed = discord.Embed(title="Auction Card", description="(embed parsing failed)")

                    # Tag as started
                    emb = card_embed.copy()
                    desc = emb.description or ""
                    desc += "\nAuction started ✅"
                    emb.description = desc
                    emb.color = discord.Color.green()

                    if release_channel:
                        m = await release_channel.send(embed=emb)
                        await conn.execute(
                            "UPDATE submissions SET status='released', released_message_id=$1, released_channel_id=$2 WHERE id=$3",
                            m.id, release_channel.id, r["id"]
                        )
                    else:
                        await conn.execute(
                            "UPDATE submissions SET status='released' WHERE id=$1",
                            r["id"]
                        )

                # mark as run today
                await self.redis.set(self.last_release_marker_key, today_key)

    @check_auctions.before_loop
    async def before_check_auctions(self):
        await self.bot.wait_until_ready()

    # --- Setup function to add cog ---
async def setup(bot: commands.Bot):
    await bot.add_cog(Auctions(bot))
