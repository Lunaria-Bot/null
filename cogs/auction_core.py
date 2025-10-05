import json
import re
import discord
from discord.ext import commands
from typing import Optional, Dict
from datetime import date

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
    # Title like "Rimuru Tempest v92" OR description **Version:** `92`
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
    # Prefer title emoji ID
    title = embed_dict.get("title") or ""
    eid = parse_emoji_id_from_text(title)
    if eid and eid in RARITY_FROM_EMOJI_ID:
        return RARITY_FROM_EMOJI_ID[eid]
    # Fallback: scan description for emojis
    desc = embed_dict.get("description") or ""
    eid2 = parse_emoji_id_from_text(desc)
    if eid2 and eid2 in RARITY_FROM_EMOJI_ID:
        return RARITY_FROM_EMOJI_ID[eid2]
    # Fallback: keywords
    joined = " ".join([
        title.upper(),
        (embed_dict.get("description") or "").upper(),
        ((embed_dict.get("footer") or {}).get("text") or "").upper()
    ])
    for key in ["UR", "SSR", "SR", "RARE", "COMMON"]:
        if key in joined:
            return key
    return None

class AuctionCore(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot and message.author.id == self.bot.mazoku_bot_id and message.channel.id == self.bot.mazoku_channel_id:
            await self._process_mazoku_embed(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot and after.author.id == self.bot.mazoku_bot_id and after.channel.id == self.bot.mazoku_channel_id:
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
            return  # On ne cache que les cartes avec Owner identifiable

        rarity = parse_rarity(data)
        version = parse_version_from_text(title_raw) or parse_version_from_text(desc)
        series = parse_series_from_desc(desc)
        batch = parse_batch_from_desc(desc)

        payload = {
            "title": title_clean,                  # e.g., "Rimuru Tempest v92"
            "rarity": rarity or "COMMON",
            "series": series,                      # e.g., "Tensei Shitara Slime Datta Ken"
            "version": version,                    # e.g., "92"
            "batch": batch,                        # e.g., 15
            "owner_id": owner_id,                  # e.g., 912376040142307419
            "image_url": image,
            "raw": data,                           # full embed dict for debug
        }

        await self.bot.redis.set(f"mazoku:card:{owner_id}", json.dumps(payload), ex=600)

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
        image_url TEXT
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
