import json
from datetime import datetime, timezone
import discord
from discord.ext import commands, tasks

from .auctions_utils import next_daily_release, RELEASE_HOUR_UTC, RELEASE_MINUTE_UTC

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
        target = now_utc.replace(hour=RELEASE_HOUR_UTC, minute=RELEASE_MINUTE_UTC, second=0, microsecond=0)
        today_key = now_utc.strftime("%Y-%m-%d")

        last_run_day = await core.redis.get(self.last_release_marker_key)
        if now_utc >= target and (last_run_day != today_key):
            async with core.pg_pool.acquire() as conn:
                # 1) Close previous released auctions not closed yet
                prev_rows = await conn.fetch(
                    "SELECT id, queue_channel_id, queue_message_id, closed FROM submissions "
                    "WHERE status='released' AND closed=FALSE"
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
                        await conn.execute("UPDATE submissions SET status='released' WHERE id=$1", r["id"])

                await core.redis.set(self.last_release_marker_key, today_key)

    @check_auctions.before_loop
    async def before_check_auctions(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionsScheduler(bot))
