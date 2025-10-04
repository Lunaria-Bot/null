# cogs/auctions.py
import os
import json
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
        return default
    try:
        return int(val)
    except ValueError:
        raise RuntimeError(f"Environment variable {name} must be an integer, got: {val}")

# --- Required IDs ---
GUILD_ID = get_int_env("GUILD_ID", required=True)

# --- Mazoku IDs and channel (fixed values per your request) ---
MAZOKU_BOT_ID = 1242388858897956906
MAZOKU_CHANNEL_ID = 1303054862447022151

# --- Optional IDs ---
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


class Auctions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.redis: redis.Redis = None
        self.pg_pool: asyncpg.Pool = None
        self.check_auctions.start()

    async def cog_load(self):
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self.pg_pool = await asyncpg.create_pool(**POSTGRES_DSN)
        async with self.pg_pool.acquire() as conn:
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                card JSONB NOT NULL,
                currency TEXT,
                rate TEXT,
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

    # --- Detect Mazoku messages (new + edits) ---
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
        desc = data.get("description", "")

        # Log raw embed for debugging
        print("🧾 Mazoku embed:", data)

        # Detect and extract owner ID from "Owned by <@ID>"
        if "Owned by <@" in desc:
            try:
                start = desc.find("Owned by <@") + len("Owned by <@")
                end = desc.find(">", start)
                owner_id = int(desc[start:end])

                # Store the full embed for this owner (expires in 10 minutes)
                await self.redis.set(
                    f"mazoku:card:{owner_id}",
                    json.dumps(data),
                    ex=600
                )
                print(f"📥 Carte Mazoku détectée et liée à l'utilisateur {owner_id}")
            except Exception as e:
                print("❗ Erreur lors du parsing de l'owner_id:", e)

    # --- Slash command: auction-submit (guild-only) ---
    @app_commands.command(name="auction-submit", description="Submit your Mazoku card for auction")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_submit(self, interaction: discord.Interaction):
        # Fetch the last detected card for the current user
        data = await self.redis.get(f"mazoku:card:{interaction.user.id}")
        if not data:
            return await interaction.response.send_message(
                "❌ No Mazoku card detected. Use `/inventory` with Mazoku first.",
                ephemeral=True
            )

        card_embed = discord.Embed.from_dict(json.loads(data))

        # DM the user with the card and the setup view
        dm = await interaction.user.create_dm()
        await dm.send(
            "Here’s the card I detected from Mazoku:",
            embed=card_embed,
            view=self.AuctionSetupView(self, interaction.user, card_embed)
        )
        await interaction.response.send_message(
            "📩 Check your DMs to finish your auction submission.",
            ephemeral=True
        )

    # --- DM Form ---
    class AuctionSetupView(discord.ui.View):
        def __init__(self, cog: "Auctions", user: discord.User, card_embed: discord.Embed):
            super().__init__(timeout=600)
            self.cog = cog
            self.user = user
            self.card_embed = card_embed
            self.currency = None
            self.rate = None

        @discord.ui.button(label="Set Currency", style=discord.ButtonStyle.blurple)
        async def set_currency(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            self.currency = "Bloodstones"
            await interaction.response.send_message("✅ Currency set to Bloodstones", ephemeral=True)

        @discord.ui.button(label="Set Rate", style=discord.ButtonStyle.gray)
        async def set_rate(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            await interaction.response.send_modal(self.cog.RateModal(self))

        @discord.ui.button(label="Submit Auction", style=discord.ButtonStyle.green)
        async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)

            async with self.cog.pg_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO submissions(user_id, card, currency, rate, status) VALUES($1,$2,$3,$4,'submitted')",
                    self.user.id,
                    self.card_embed.to_dict(),
                    self.currency,
                    self.rate
                )

            await interaction.response.send_message("✅ Auction submitted!", ephemeral=True)
            await self.user.send("Your auction has been submitted and is pending review.")
            self.stop()

    class RateModal(discord.ui.Modal, title="Set Auction Rate"):
        rate = discord.ui.TextInput(label="Rate (e.g. 275:1)", required=True)

        def __init__(self, parent_view: "Auctions.AuctionSetupView"):
            super().__init__()
            self.parent_view = parent_view

        async def on_submit(self, interaction: discord.Interaction):
            self.parent_view.rate = self.rate.value
            await interaction.response.send_message(f"✅ Rate set to {self.rate.value}", ephemeral=True)

    # --- Staff review (guild-only) ---
    @app_commands.command(name="auction-review", description="Review a submission")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_review(self, interaction: discord.Interaction, submission_id: int):
        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM submissions WHERE id=$1", submission_id)
        if not row:
            return await interaction.response.send_message("❌ Submission not found.", ephemeral=True)

        embed = discord.Embed(title=f"Auction Submission #{submission_id}", color=discord.Color.gold())
        embed.add_field(name="User", value=f"<@{row['user_id']}>", inline=True)

        if row["card"]:
            try:
                card_embed = discord.Embed.from_dict(row["card"])
                embed.description = card_embed.title or ""
                if card_embed.image:
                    embed.set_image(url=card_embed.image.url)
            except Exception:
                pass

        if row["currency"]:
            embed.add_field(name="Currency", value=row["currency"], inline=True)
        if row["rate"]:
            embed.add_field(name="Rate", value=row["rate"], inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- Scheduler loop ---
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
                        if row["card"]:
                            try:
                                card_embed = discord.Embed.from_dict(row["card"])
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
