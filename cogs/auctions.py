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
    """Safely load an environment variable as int."""
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
GUILD_ID = get_int_env("GUILD_ID")
MAZOKU_BOT_ID = get_int_env("MAZOKU_BOT_ID")
AUCTION_CHANNEL_ID = get_int_env("AUCTION_CHANNEL_ID")

# --- Optional IDs ---
STAFF_ALERT_CHANNEL_ID = get_int_env("STAFF_ALERT_CHANNEL_ID", required=False, default=0)
STAFF_ROLE_ID = get_int_env("STAFF_ROLE_ID", required=False, default=0)

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
    def __init__(self, bot):
        self.bot = bot
        self.redis = None
        self.pg_pool = None
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

    # --- Detect Mazoku messages ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot and message.author.id == MAZOKU_BOT_ID:
            if message.embeds:
                embed = message.embeds[0]
                if embed.image and message.mentions:
                    user = message.mentions[0]
                    await self.redis.set(f"mazoku:{user.id}:card", json.dumps(embed.to_dict()), ex=600)
                    await user.send("📩 I detected your Mazoku card. Use `/auction-submit` to continue.")

    # --- Slash command: auction-submit ---
    @app_commands.command(name="auction-submit", description="Submit your Mazoku card for auction")
    async def auction_submit(self, interaction: discord.Interaction):
        data = await self.redis.get(f"mazoku:{interaction.user.id}:card")
        if not data:
            return await interaction.response.send_message(
                "❌ No Mazoku card detected. Use `/inventory` with Mazoku first.", ephemeral=True
            )

        card_embed = discord.Embed.from_dict(json.loads(data))
        dm = await interaction.user.create_dm()
        await dm.send("Here’s the card I detected from Mazoku:", embed=card_embed,
                      view=self.AuctionSetupView(self, interaction.user, card_embed))
        await interaction.response.send_message("📩 Check your DMs to finish your auction submission.", ephemeral=True)

    # --- DM Form ---
    class AuctionSetupView(discord.ui.View):
        def __init__(self, cog, user, card_embed):
            super().__init__(timeout=600)
            self.cog, self.user, self.card_embed = cog, user, card_embed
            self.currency, self.rate = None, None

        @discord.ui.button(label="Set Currency", style=discord.ButtonStyle.blurple)
        async def set_currency(self, interaction, button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            self.currency = "Bloodstones"  # could be a Select menu
            await interaction.response.send_message("✅ Currency set to Bloodstones", ephemeral=True)

        @discord.ui.button(label="Set Rate", style=discord.ButtonStyle.gray)
        async def set_rate(self, interaction, button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            await interaction.response.send_modal(self.cog.RateModal(self))

        @discord.ui.button(label="Submit Auction", style=discord.ButtonStyle.green)
        async def submit(self, interaction, button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            async with self.cog.pg_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO submissions(user_id, card, currency, rate, status) VALUES($1,$2,$3,$4,'submitted')",
                    self.user.id, self.card_embed.to_dict(), self.currency, self.rate
                )
            await interaction.response.send_message("✅ Auction submitted!", ephemeral=True)
            await self.user.send("Your auction has been submitted and is pending review.")
            self.stop()

    class RateModal(discord.ui.Modal, title="Set Auction Rate"):
        rate = discord.ui.TextInput(label="Rate (e.g. 275:1)", required=True)
        def __init__(self, parent_view): super().__init__(); self.parent_view = parent_view
        async def on_submit(self, interaction):
            self.parent_view.rate = self.rate.value
            await interaction.response.send_message(f"✅ Rate set to {self.rate.value}", ephemeral=True)

    # --- Staff review ---
    @app_commands.command(name="auction-review", description="Review a submission")
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
                if card_embed.image: embed.set_image(url=card_embed.image.url)
            except: pass
        if row["currency"]: embed.add_field(name="Currency", value=row["currency"], inline=True)
        if row["rate"]: embed.add_field(name="Rate", value=row["rate"], inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- Scheduler loop ---
    @tasks.loop(minutes=1)
    async def check_auctions(self):
        now = datetime.now(timezone.utc)
        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM submissions WHERE status='approved' AND scheduled_for <= $1", now)
            for row in rows:
                channel = self.bot.get_channel(AUCTION_CHANNEL_ID)
                if channel:
                    embed = discord.Embed(title="Auction Started!", color=discord.Color.green())
                    if row["card"]:
                        try:
                            card_embed = discord.Embed.from_dict(row["card"])
                            embed.description = card_embed.title or ""
                            if card_embed.image: embed.set_image(url=card_embed.image.url)
                        except: pass
                    await channel.send(embed=embed)
                    await conn.execute("UPDATE submissions SET status='released' WHERE id=$1", row["id"])


async def setup(bot): 
    await bot.add_cog(Auctions(bot))
