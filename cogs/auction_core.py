import json
import re
import discord
from discord.ext import commands
from typing import Optional, Dict
from datetime import date

# Pour la commande admin /auction-force-ready
from discord import app_commands

RARITY_FROM_EMOJI_ID = {
    1342202203515125801: "UR",
    1342202212948115510: "SSR",
    1342202597389373530: "SR",
    1342202219574857788: "RARE",
    1342202221558763571: "COMMON",
}

def strip_discord_emojis(text: str) -> str:
    return re.sub(r"<a?:\w+:\d+>", "", text or "").strip()

def parse_emoji_id_from_text(text: str) -> Optional[int]:
    m = re.search(r"<a?:\w+:(\d+)>", text or "")
    return int(m.group(1)) if m else None

def parse_version_from_text(text: str) -> Optional[str]:
    m = re.search(r"\bv(\d+)\b", text or "", flags=re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r"Version:\s*`?(\d+)`?", text or "", flags=re.IGNORECASE)
    return m2.group(1) if m2 else None

def parse_series_from_desc(desc: str) -> Optional[str]:
    m = re.search(r"\*\*Series:\*\*\s*(.+)", desc or "")
    return m.group(1).strip() if m else None

def parse_batch_from_desc(desc: str) -> Optional[int]:
    m = re.search(r"Batch\s+(\d+)", desc or "", flags=re.IGNORECASE)
    return int(m.group(1)) if m else None

def parse_owner_id_from_desc(desc: str) -> Optional[int]:
    m = re.search(r"Owned by <@(\d+)>", desc or "")
    return int(m.group(1)) if m else None

def parse_rarity(embed_dict: Dict) -> Optional[str]:
    title = embed_dict.get("title") or ""
    eid = parse_emoji_id_from_text(title)
    if eid and eid in RARITY_FROM_EMOJI_ID:
        return RARITY_FROM_EMOJI_ID[eid]
    desc = embed_dict.get("description") or ""
    eid2 = parse_emoji_id_from_text(desc)
    if eid2 and eid2 in RARITY_FROM_EMOJI_ID:
        return RARITY_FROM_EMOJI_ID[eid2]
    joined = " ".join([
        title.upper(),
        (embed_dict.get("description") or "").upper(),
        ((embed_dict.get("footer") or {}).get("text") or "").upper()
    ])
    for key in ["UR", "SSR", "SR", "RARE", "COMMON"]:
        if key in joined:
            return key
    return None

# --- NEW: parseur Event / Special ---
def parse_event_or_special(embed_dict: Dict) -> Dict[str, Optional[str]]:
    title = (embed_dict.get("title") or "").lower()
    desc = (embed_dict.get("description") or "").lower()
    footer = ((embed_dict.get("footer") or {}).get("text") or "").lower()
    text = " ".join([title, desc, footer])

    event_icon = None
    special_icon = None

    # 🔹 Event
    if "🔹" in (embed_dict.get("title") or "") or "🔹" in (embed_dict.get("description") or "") or "🔹" in footer:
        if "christmas" in text:
            event_icon = "🎄"
        elif "halloween" in text:
            event_icon = "🎃"
        elif "maid" in text:
            event_icon = "<:maidbow:1399426280549777439>"
        elif "summer" in text:
            event_icon = "🏖️"

    # 🔸 Special
    if "🔸" in (embed_dict.get("title") or "") or "🔸" in (embed_dict.get("description") or "") or "🔸" in footer:
        if "special" in text:
            special_icon = "✨"

    return {"event": event_icon, "special": special_icon}
# --- Log utilitaire quand une carte entre en waiting list (READY) ---
async def log_card_ready(bot: commands.Bot, auction: Dict):
    guild = bot.get_guild(bot.guild_id)
    if not guild:
        return
    log_channel = guild.get_channel(bot.log_channel_id)
    if not log_channel:
        return

    card_name = auction["title"] or (
        f"{auction['series']} v{auction['version']}" if auction.get("series") and auction.get("version") else f"Auction #{auction['id']}"
    )

    embed = discord.Embed(
        title="Card added to waiting list",
        color=discord.Color.green()
    )
    embed.add_field(name="Name of the card", value=card_name, inline=True)
    embed.add_field(name="Version", value=auction.get("version") or "?", inline=True)
    embed.add_field(name="Queue", value=auction.get("queue_type") or "?", inline=True)
    embed.add_field(name="Seller", value=f"<@{auction['user_id']}>", inline=True)
    embed.add_field(name="Rarity", value=auction.get("rarity") or "?", inline=True)
    embed.add_field(name="Currency", value=auction.get("currency") or "N/A", inline=True)
    embed.add_field(name="Rate", value=auction.get("rate") or "N/A", inline=True)
    if auction.get("event"):
        embed.add_field(name="Event", value=auction["event"], inline=True)
    if auction.get("special"):
        embed.add_field(name="Special", value=auction["special"], inline=True)
    if auction.get("image_url"):
        embed.set_image(url=auction["image_url"])

    await log_channel.send(embed=embed)

# --- Helper: marquer une enchère READY et loguer ---
async def mark_auction_ready(bot: commands.Bot, pool, auction_id: int):
    await pool.execute("UPDATE auctions SET status='READY' WHERE id=$1", auction_id)
    auction = await pool.fetchrow("SELECT * FROM auctions WHERE id=$1", auction_id)
    if auction:
        await log_card_ready(bot, dict(auction))
    return auction

class AuctionCore(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot and message.author.id == self.bot.mazoku_bot_id:
            await self._process_mazoku_embed(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot and after.author.id == self.bot.mazoku_bot_id:
            await self._process_mazoku_embed(after)

    async def _process_mazoku_embed(self, message: discord.Message):
        if not message.embeds:
            return
        emb = message.embeds[0]
        data = emb.to_dict()

        title_raw = data.get("title") or ""
        title_clean = strip_discord_emojis(title_raw)
        desc = data.get("description") or ""
        image = (data.get("image") or {}).get("url")

        owner_id = parse_owner_id_from_desc(desc)
        if not owner_id:
            return

        rarity = parse_rarity(data)
        version = parse_version_from_text(title_raw) or parse_version_from_text(desc)
        series = parse_series_from_desc(desc)
        batch = parse_batch_from_desc(desc)

        # --- NEW: detect event/special
        event_info = parse_event_or_special(data)

        payload = {
            "title": title_clean,
            "rarity": rarity or "COMMON",
            "series": series,
            "version": version,
            "batch": batch,
            "owner_id": owner_id,
            "image_url": image,
            "raw": data,
            "event": event_info.get("event"),
            "special": event_info.get("special"),
        }

        await self.bot.redis.set(f"mazoku:card:{owner_id}", json.dumps(payload), ex=600)

    @app_commands.command(name="auction-force-ready", description="Force an auction to READY and log it (admin).")
    @app_commands.default_permissions(administrator=True)
    async def auction_force_ready(self, interaction: discord.Interaction, auction_id: int):
        await interaction.response.defer(ephemeral=True)
        auction = await mark_auction_ready(self.bot, self.bot.pg, auction_id)
        if auction:
            await interaction.followup.send(f"✅ Auction #{auction_id} forced to READY and logged.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Auction #{auction_id} not found.", ephemeral=True)

# --- Initialisation DB ---
async def init_db(pool):
    await pool.execute("""
    CREATE TABLE IF NOT EXISTS auctions (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        series TEXT,
        version TEXT,
        batch_no INT,
        owner_id BIGINT,
        rarity TEXT,
        queue_type TEXT,
        currency TEXT,
        rate TEXT,
        status TEXT DEFAULT 'PENDING',
        created_at TIMESTAMPTZ DEFAULT NOW(),
        title TEXT,
        image_url TEXT,
        event TEXT,
        special TEXT
    );
    CREATE TABLE IF NOT EXISTS batches (
        id SERIAL PRIMARY KEY,
        batch_date DATE UNIQUE NOT NULL,
        locked_at TIMESTAMPTZ,
        posted_at TIMESTAMPTZ
    );
    CREATE TABLE IF NOT EXISTS batch_items (
        id SERIAL PRIMARY KEY,
        batch_id INT REFERENCES batches(id) ON DELETE CASCADE,
        auction_id INT REFERENCES auctions(id) ON DELETE CASCADE,
        position INT
    );
    CREATE TABLE IF NOT EXISTS reviews (
        id SERIAL PRIMARY KEY,
        auction_id INT REFERENCES auctions(id) ON DELETE CASCADE,
        stage INT NOT NULL,
        reviewer_id BIGINT NOT NULL,
        decision TEXT NOT NULL,
        reason TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """)
    return True

async def get_or_create_today_batch(pool) -> int:
    today = date.today()
    rec = await pool.fetchrow("SELECT id FROM batches WHERE batch_date=$1", today)
    if rec:
        return rec["id"]
    rec = await pool.fetchrow("INSERT INTO batches (batch_date) VALUES ($1) RETURNING id", today)
    return rec["id"]

async def lock_today_batch(pool):
    today = date.today()
    await pool.execute("UPDATE batches SET locked_at=NOW() WHERE batch_date=$1 AND locked_at IS NULL", today)

async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionCore(bot))
