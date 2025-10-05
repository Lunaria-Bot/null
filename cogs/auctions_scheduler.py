import json
from datetime import datetime, timezone
import discord
from discord.ext import commands, tasks

# --- Configuration (UTC) ---
RELEASE_HOUR_UTC = 15
RELEASE_MINUTE_UTC = 57

# Forums par rareté
RARITY_FORUMS = {
    "common": 1342202221558763571,
    "rare": 1342202219574857788,
    "sr": 1342202597389373530,
    "ssr": 1342202212948115510,
    "ur": 1342202203515125801,
}


class AuctionsScheduler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_release_marker_key = "auctions:last_release_date"
        self.check_auctions.start()

    async def cog_unload(self):
        self.check_auctions.cancel()

    @tasks.loop(minutes=1)
    async def check_auctions(self):
        core = self.bot.get_cog("AuctionsCore")
        if core is None or core.pg_pool is None or core.redis is None:
            return

        now_utc = datetime.now(timezone.utc)
        target_today = now_utc.replace(
            hour=RELEASE_HOUR_UTC, minute=RELEASE_MINUTE_UTC, second=0, microsecond=0
        )

        today_key = now_utc.strftime("%Y-%m-%d")
        last_run_day = await core.redis.get(self.last_release_marker_key)

        if now_utc >= target_today and (last_run_day != today_key):
            async with core.pg_pool.acquire() as conn:
                # 1) Fermer les enchères précédentes
                prev_rows = await conn.fetch(
                    "SELECT id, queue_channel_id, queue_message_id "
                    "FROM submissions WHERE status='released' AND closed=FALSE"
                )
                for r in prev_rows:
                    ch = self.bot.get_channel(r["queue_channel_id"]) if r["queue_channel_id"] else None
                    if ch and r["queue_message_id"]:
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

                # 2) Publier les nouvelles enchères
                new_rows = await conn.fetch(
                    "SELECT id, card, rarity FROM submissions WHERE status='accepted' AND scheduled_for <= $1",
                    now_utc
                )

                for r in new_rows:
                    try:
                        card_dict = r["card"] if isinstance(r["card"], dict) else json.loads(r["card"])
                        card_embed = discord.Embed.from_dict(card_dict)
                    except Exception:
                        card_embed = discord.Embed(title="Auction Card", description="(embed parsing failed)")

                    rarity = r["rarity"] or "common"
                    forum_id = RARITY_FORUMS.get(rarity, RARITY_FORUMS["common"])
                    forum = self.bot.get_channel(forum_id)

                    if forum and isinstance(forum, discord.ForumChannel):
                        thread = await forum.create_thread(
                            name=f"Auction {r['id']} – {card_embed.title or 'Card'}",
                            content="Auction started ✅",
                            embed=card_embed
                        )
                        await conn.execute(
                            "UPDATE submissions SET status='released', released_channel_id=$1, released_thread_id=$2 WHERE id=$3",
                            forum.id, thread.id, r["id"]
                        )
                    else:
                        print(f"❌ Forum not found for rarity {rarity}")
                        await conn.execute(
                            "UPDATE submissions SET status='released' WHERE id=$1",
                            r["id"]
                        )

                # Marquer la release du jour
                await core.redis.set(self.last_release_marker_key, today_key)

    @check_auctions.before_loop
    async def before_check_auctions(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionsScheduler(bot))
