import os
import json
import re
from datetime import datetime, timezone
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

# --- Queue channels (configure in .env) ---
QUEUE_CARDMAKER_ID = get_int_env("QUEUE_CARDMAKER_ID", required=False, default=0)
QUEUE_NORMAL_ID = get_int_env("QUEUE_NORMAL_ID", required=False, default=0)
QUEUE_SKIP_ID = get_int_env("QUEUE_SKIP_ID", required=False, default=0)

# --- Optional auction release channel ---
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


class Auctions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.redis: redis.Redis = None
        self.pg_pool: asyncpg.Pool = None
        self.check_auctions.start()

    async def cog_load(self):
        if not REDIS_URL:
            raise RuntimeError("REDIS_URL is not set")
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self.pg_pool = await asyncpg.create_pool(**POSTGRES_DSN)

        # Ensure schema (fresh, aligned to code)
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

    async def cog_unload(self):
        if self.redis:
            await self.redis.close()
        if self.pg_pool:
            await self.pg_pool.close()
        self.check_auctions.cancel()

    # --- Detect Mazoku messages ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            message.author.bot
            and message.author.id == MAZOKU_BOT_ID
            and message.channel.id == MAZOKU_CHANNEL_ID
        ):
            await self._process_mazoku_embed(message)

    @commands.Cog.listener()
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
                await self.redis.set(
                    f"mazoku:card:{owner_id}",
                    json.dumps(data),
                    ex=600
                )
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
        await dm.send(
            "Here is your submitted card",
            embed=card_embed,
            view=self.AuctionSetupView(self, interaction.user, card_embed)
        )
        await interaction.response.send_message("📩 Check your DMs to finish your auction submission.", ephemeral=True)

    # --- DM Form ---
    class AuctionSetupView(discord.ui.View):
        def __init__(self, cog: "Auctions", user: discord.User, card_embed: discord.Embed):
            super().__init__(timeout=600)
            self.cog = cog
            self.user = user
            self.card_embed = card_embed
            self.currency = None
            self.rate = None
            self.queue_choice = None

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

        @discord.ui.button(label="Set Currency", style=discord.ButtonStyle.blurple)
        async def set_currency(self, interaction, button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            await interaction.response.send_modal(self.cog.CurrencyModal(self))

        @discord.ui.button(label="Set Rate", style=discord.ButtonStyle.gray)
        async def set_rate(self, interaction, button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            await interaction.response.send_modal(self.cog.RateModal(self))

        @discord.ui.button(label="Submit Auction", style=discord.ButtonStyle.green)
        async def submit(self, interaction, button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            if not self.queue_choice or not self.currency or not self.rate:
                return await interaction.response.send_message("❌ Please fill all fields.", ephemeral=True)

            # Insert into DB and get submission_id
            submission_id = None
            try:
                async with self.cog.pg_pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "INSERT INTO submissions(user_id, card, currency, rate, queue, status) "
                        "VALUES($1,$2::jsonb,$3,$4,$5,'submitted') RETURNING id",
                        self.user.id,
                        json.dumps(self.card_embed.to_dict()),  # JSONB
                        self.currency,
                        self.rate,
                        self.queue_choice
                    )
                    if row:
                        submission_id = row["id"]
            except Exception as e:
                print("❗ DB insert error:", e)

            if not submission_id:
                return await interaction.response.send_message(
                    "❌ Database error: submission not saved.", ephemeral=True
                )

            # Resolve channel
            if self.queue_choice == "cardmaker":
                channel_id = QUEUE_CARDMAKER_ID
            elif self.queue_choice == "normal":
                channel_id = QUEUE_NORMAL_ID
            else:
                channel_id = QUEUE_SKIP_ID

            channel = self.cog.bot.get_channel(channel_id) if channel_id else None
            if not channel:
                return await interaction.response.send_message(
                    "❌ Queue channel is not configured. Please contact staff.", ephemeral=True
                )

            # Post the embed to the queue channel with staff controls
            try:
                await channel.send(
                    embed=self.card_embed,
                    view=self.cog.StaffReviewView(self.cog, submission_id, self.queue_choice)
                )
            except Exception as e:
                print("❗ Error sending to queue channel:", e)
                return await interaction.response.send_message(
                    "❌ Failed to post in the selected queue.", ephemeral=True
                )

            # Confirmation embed in DM
            confirmation = discord.Embed(
                title="🎉 Auction Submission Successful 🎉",
                color=discord.Color.green()
            )
            confirmation.add_field(name="Card", value=self.card_embed.title or "Unknown", inline=False)
            confirmation.add_field(name="Currency", value=self.currency, inline=True)
            confirmation.add_field(name="Rate", value=self.rate, inline=True)
            confirmation.add_field(name="Queue", value=self.queue_choice.capitalize(), inline=True)
            confirmation.set_footer(text="Good luck with your auction! • 🌟 • " + datetime.now().strftime("%d/%m/%Y %H:%M"))

            await interaction.response.edit_message(content=None, embed=confirmation, view=None)
            self.stop()

    # --- Modals ---
    class CurrencyModal(discord.ui.Modal, title="Set Currency"):
        currency = discord.ui.TextInput(label="Currency", required=True)

        def __init__(self, parent_view: "Auctions.AuctionSetupView"):
            super().__init__()
            self.parent_view = parent_view

        async def on_submit(self, interaction: discord.Interaction):
            self.parent_view.currency = self.currency.value
            for child in self.parent_view.children:
                if isinstance(child, discord.ui.Button) and child.label.startswith("Set Currency"):
                    child.label = self.currency.value
                    break
            await interaction.response.edit_message(view=self.parent_view)

    class RateModal(discord.ui.Modal, title="Set Auction Rate"):
        rate = discord.ui.TextInput(label="Rate (e.g. 175:1)", required=True)

        def __init__(self, parent_view: "Auctions.AuctionSetupView"):
            super().__init__()
            self.parent_view = parent_view

        async def on_submit(self, interaction: discord.Interaction):
            self.parent_view.rate = self.rate.value
            for child in self.parent_view.children:
                if isinstance(child, discord.ui.Button) and child.label.startswith("Set Rate"):
                    child.label = self.rate.value
                    break
            await interaction.response.edit_message(view=self.parent_view)

    # --- Staff review view attached to queue messages ---
    class StaffReviewView(discord.ui.View):
        def __init__(self, cog: "Auctions", submission_id: int, queue_choice: str):
            super().__init__(timeout=None)
            self.cog = cog
            self.submission_id = submission_id
            self.queue_choice = queue_choice

        @discord.ui.button(label="Fees Paid", style=discord.ButtonStyle.green)
        async def fees_paid_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.submission_id:
                return await interaction.response.send_message("❌ Submission not found.", ephemeral=True)
            try:
                async with self.cog.pg_pool.acquire() as conn:
                    await conn.execute("UPDATE submissions SET fees_paid = TRUE WHERE id=$1", self.submission_id)
                await interaction.response.send_message("✅ Fees marked as paid", ephemeral=True)
            except Exception as e:
                print("❗ Error updating fees:", e)
                await interaction.response.send_message("❌ Failed to update fees.", ephemeral=True)

        @discord.ui.button(label="Fees Not Paid", style=discord.ButtonStyle.red)
        async def fees_not_paid_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.submission_id:
                return await interaction.response.send_message("❌ Submission not found.", ephemeral=True)
            try:
                async with self.cog.pg_pool.acquire() as conn:
                    await conn.execute("UPDATE submissions SET fees_paid = FALSE WHERE id=$1", self.submission_id)
                await interaction.response.send_message("❌ Fees marked as not paid", ephemeral=True)
            except Exception as e:
                print("❗ Error updating fees:", e)
                await interaction.response.send_message("❌ Failed to update fees.", ephemeral=True)

        @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
        async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.submission_id:
                return await interaction.response.send_message("❌ Submission not found.", ephemeral=True)
            try:
                batch_id = await self.cog.add_to_batch(self.submission_id, self.queue_choice)
                await interaction.response.send_message(f"✅ Card accepted (Batch {batch_id})", ephemeral=True)
            except Exception as e:
                print("❗ Error accepting card:", e)
                await interaction.response.send_message("❌ Failed to accept card.", ephemeral=True)

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
        async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.submission_id:
                return await interaction.response.send_message("❌ Submission not found.", ephemeral=True)
            try:
                async with self.cog.pg_pool.acquire() as conn:
                    await conn.execute("UPDATE submissions SET status='denied' WHERE id=$1", self.submission_id)
                await interaction.response.send_message("❌ Card denied", ephemeral=True)
            except Exception as e:
                print("❗ Error denying card:", e)
                await interaction.response.send_message("❌ Failed to deny card.", ephemeral=True)

        @discord.ui.button(label="Deny with reason", style=discord.ButtonStyle.gray)
        async def deny_reason_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.submission_id:
                return await interaction.response.send_message("❌ Submission not found.", ephemeral=True)
            await interaction.response.send_modal(self.cog.DenyReasonModal(self.cog, self.submission_id))

    class DenyReasonModal(discord.ui.Modal, title="Deny with reason"):
        reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, required=True)

        def __init__(self, cog: "Auctions", submission_id: int):
            super().__init__()
            self.cog = cog
            self.submission_id = submission_id

        async def on_submit(self, interaction: discord.Interaction):
            try:
                async with self.cog.pg_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE submissions SET status='denied', deny_reason=$1 WHERE id=$2",
                        self.reason.value, self.submission_id
                    )
                await interaction.response.send_message("❌ Card denied with reason saved.", ephemeral=True)
            except Exception as e:
                print("❗ Error denying with reason:", e)
                await interaction.response.send_message("❌ Failed to deny card.", ephemeral=True)

    # --- Batch management ---
    async def add_to_batch(self, submission_id: int, queue_choice: str) -> int:
        """
        Assigns the submission to a batch.
        - Normal Queue: max 15 cards per batch, create new when full.
        - Skip Queue: no limit.
        - Card Maker Queue: no limit.
        Returns the batch_id used.
        """
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
                batch_id = 1

            await conn.execute(
                "UPDATE submissions SET status='accepted', batch_id=$1 WHERE id=$2",
                batch_id, submission_id
            )
            return batch_id

    # --- Staff review (slash command to inspect) ---
    @app_commands.command(name="auction-review", description="Review a submission")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_review(self, interaction: discord.Interaction, submission_id: int):
        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM submissions WHERE id=$1", submission_id)
        if not row:
            return await interaction.response.send_message("❌ Submission not found.", ephemeral=True)

        embed = discord.Embed(title=f"Auction Submission #{submission_id}", color=discord.Color.gold())
        embed.add_field(name="User", value=f"<@{row['user_id']}>", inline=True)

        card_data = row["card"]
        try:
            if isinstance(card_data, str):
                card_dict = json.loads(card_data)
            else:
                card_dict = dict(card_data) if card_data is not None else None
        except Exception:
            card_dict = None

        if card_dict:
            try:
                card_embed = discord.Embed.from_dict(card_dict)
                embed.description = card_embed.title or ""
                if card_embed.image:
                    embed.set_image(url=card_embed.image.url)
            except Exception:
                pass

        if row["currency"]:
            embed.add_field(name="Currency", value=row["currency"], inline=True)
        if row["rate"]:
            embed.add_field(name="Rate", value=row["rate"], inline=True)
        if row["queue"]:
            embed.add_field(name="Queue", value=row["queue"], inline=True)
        if row["batch_id"]:
            embed.add_field(name="Batch", value=str(row["batch_id"]), inline=True)
        if row["fees_paid"] is not None:
            embed.add_field(name="Fees", value="Paid" if row["fees_paid"] else "Not paid", inline=True)
        if row["deny_reason"]:
            embed.add_field(name="Deny reason", value=row["deny_reason"], inline=False)
        if row["status"]:
            embed.add_field(name="Status", value=row["status"], inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- Scheduler loop (optional release flow) ---
    @tasks.loop(minutes=1)
    async def check_auctions(self):
        now = datetime.now(timezone.utc)
        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM submissions WHERE status='approved' AND scheduled_for <= $1",
                now
            )
            for row in rows:
                if AUCTION_CHANNEL_ID:
                    channel = self.bot.get_channel(AUCTION_CHANNEL_ID)
                    if channel:
                        embed = discord.Embed(title="Auction Started!", color=discord.Color.green())
                        card_data = row["card"]
                        try:
                            if isinstance(card_data, str):
                                card_dict = json.loads(card_data)
                            else:
                                card_dict = dict(card_data) if card_data is not None else None
                        except Exception:
                            card_dict = None

                        if card_dict:
                            try:
                                card_embed = discord.Embed.from_dict(card_dict)
                                embed.description = card_embed.title or ""
                                if card_embed.image:
                                    embed.set_image(url=card_embed.image.url)
                            except Exception:
                                pass

                        await channel.send(embed=embed)
                        await conn.execute(
                            "UPDATE submissions SET status='released' WHERE id=$1",
                            row["id"]
                        )

    @check_auctions.before_loop
    async def before_check_auctions(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Auctions(bot))
