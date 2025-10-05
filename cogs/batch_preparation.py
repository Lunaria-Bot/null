# cogs/batch_preparation.py
import datetime
import discord
from discord.ext import commands
from config.settings import (
    DAILY_BATCH_SIZE_NORMAL, TIMEZONE,
    FORUM_COMMON, FORUM_RARE, FORUM_SR, FORUM_SSR, FORUM_UR
)

RARITY_TO_FORUM = {
    "Common": FORUM_COMMON,
    "Rare": FORUM_RARE,
    "SR": FORUM_SR,
    "SSR": FORUM_SSR,
    "UR": FORUM_UR,
}

class BatchPreparation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _get_or_open_today_batch(self):
        today = datetime.datetime.now(TIMEZONE).date()
        batch = await self.bot.pg.fetchrow("SELECT * FROM batches WHERE date=$1", today)
        if not batch:
            bid = await self.bot.pg.fetchval("INSERT INTO batches(date, status) VALUES($1,'Open') RETURNING id", today)
            return {"id": bid, "date": today, "status": "Open"}
        return dict(batch)

    async def add_to_batch_if_accepted(self, submission_id: int):
        sub = await self.bot.pg.fetchrow("SELECT * FROM submissions WHERE id=$1", submission_id)
        if not sub or sub["status"] != "Accepted":
            return

        batch = await self._get_or_open_today_batch()

        # Respecter la limite de 15 pour Normal queue
        if sub["queue"] == "Normal":
            count_normal = await self.bot.pg.fetchval("""
                SELECT COUNT(*) FROM batch_items bi
                JOIN submissions s ON s.id = bi.submission_id
                WHERE bi.batch_id=$1 AND s.queue='Normal'
            """, batch["id"])
            if count_normal >= DAILY_BATCH_SIZE_NORMAL:
                return  # On peut logguer / notifier

        rarity = await self.bot.pg.fetchval("""
            SELECT rarity FROM cards WHERE id=$1
        """, sub["card_id"]) or "Common"

        await self.bot.pg.execute(
            "INSERT INTO batch_items(batch_id, submission_id, rarity) VALUES($1,$2,$3)",
            batch["id"], submission_id, rarity
        )

    async def post_card_thread(self, submission_id: int):
        sub = await self.bot.pg.fetchrow("""
            SELECT s.id, s.user_id, s.queue, c.title, c.rarity, c.image_url
            FROM submissions s JOIN cards c ON s.card_id = c.id
            WHERE s.id=$1
        """, submission_id)
        if not sub:
            return

        rarity = sub["rarity"] or "Common"
        forum_id = RARITY_TO_FORUM.get(rarity, FORUM_COMMON)
        forum = self.bot.get_channel(forum_id)
        if not forum or not isinstance(forum, discord.ForumChannel):
            return

        # Crée un post de forum par carte
        thread = await forum.create_thread(
            name=f"{sub['title']} [{rarity}]",
            content=f"Submitted by <@{sub['user_id']}> — Queue: {sub['queue']}",
        )
        if sub["image_url"]:
            await thread.thread.send(sub["image_url"])

        await self.bot.pg.execute(
            "UPDATE submissions SET forum_thread_id=$1 WHERE id=$2",
            thread.id, submission_id
        )

    async def summarize_posts(self):
        # Résumé vers auction pings
        channel = self.bot.get_channel(1303363406404915261)
        if not channel:
            return
        today = datetime.datetime.now(TIMEZONE).date()
        rows = await self.bot.pg.fetch("""
            SELECT s.id, c.title, c.raw_embed, s.forum_thread_id
            FROM submissions s JOIN cards c ON s.card_id = c.id
            WHERE s.status='Accepted' AND s.forum_thread_id IS NOT NULL
              AND s.created_at::date = $1
        """, today)

        if not rows:
            return

        lines = []
        for r in rows:
            embed = r["raw_embed"]
            version = ""
            try:
                ed = dict(embed)
                # Try fields or description for version markers
                if ed.get("fields"):
                    for f in ed["fields"]:
                        if f.get("name","").lower().startswith("version"):
                            version = f.get("value","")
                            break
                if not version and ed.get("title"):
                    # e.g. "Hinata Hyuuga v11"
                    t = ed["title"]
                    if " v" in t.lower():
                        version = t.split(" v")[-1]
            except Exception:
                pass
            url = f"https://discord.com/channels/{channel.guild.id}/{r['forum_thread_id']}"
            lines.append(f"[{r['title']} + {version}]({url})")

        await channel.send("\n".join(lines))
