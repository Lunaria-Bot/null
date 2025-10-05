import discord
from discord.ext import commands
from typing import Optional, Dict
from datetime import datetime, timezone, date

RARITY_EMOJI_IDS = {
    "COMMON": 1342202221558763571,
    "RARE": 1342202219574857788,
    "SR": 1342202597389373530,
    "SSR": 1342202212948115510,
    "UR": 1342202203515125801,
}

QUEUE_TYPES = ("NORMAL", "SKIP", "CARD_MAKER")

def strip_discord_emojis(text: str) -> str:
    # Simple remover: strips <:name:id> formats
    import re
    return re.sub(r"<a?:\w+:\d+>", "", text or "").strip()

def parse_rarity_from_embed(embed_dict: Dict) -> Optional[str]:
    # Try in title or description or footer
    text_blobs = [
        embed_dict.get("title", ""),
        embed_dict.get("description", ""),
        (embed_dict.get("footer", {}) or {}).get("text", ""),
    ]
    joined = " ".join(tb.upper() for tb in text_blobs)
    for key in ["UR", "SSR", "SR", "RARE", "COMMON"]:
        if key in joined:
            return key
    return None

class AuctionCore(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            message.author.bot
            and message.author.id == self.bot.mazoku_bot_id
            and message.channel.id == self.bot.mazoku_channel_id
        ):
            await self._process_mazoku_embed(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if (
            after.author.bot
            and after.author.id == self.bot.mazoku_bot_id
            and after.channel.id == self.bot.mazoku_channel_id
        ):
            await self._process_mazoku_embed(after)

    async def _process_mazoku_embed(self, message: discord.Message):
        if not message.embeds:
            return
        embed = message.embeds[0]
        data = embed.to_dict()
        # Sanitize title
        if "title" in data and data["title"]:
            data["title"] = strip_discord_emojis(data["title"])
        desc = data.get("description") or ""
        if "Owned by <@" in desc:
            try:
                start = desc.find("Owned by <@") + len("Owned by <@")
                end = desc.find(">", start)
                owner_id = int(desc[start:end])
                # Add parsed rarity for later use
                rarity = parse_rarity_from_embed(data)
                data["parsed_rarity"] = rarity
                # Cache 10 min
                await self.bot.redis.set(f"mazoku:card:{owner_id}", str(data), ex=600)
            except Exception as e:
                print("Error parsing owner_id:", e)

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
        status TEXT DEFAULT 'PENDING', -- PENDING, ACCEPTED, DENIED, READY, POSTED
        created_at TIMESTAMPTZ DEFAULT NOW()
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
        decision TEXT NOT NULL, -- accept/deny
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
