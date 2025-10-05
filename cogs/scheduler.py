import discord
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo
from datetime import datetime, date, timedelta
from .auction_core import lock_today_batch, get_or_create_today_batch
from .utils import rarity_to_forum_id

CEST = ZoneInfo("Europe/Paris")

class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    @tasks.loop(minutes=1)
    async def tick(self):
        now = datetime.now(CEST)
        if now.hour == 17 and now.minute == 30:
            await lock_today_batch(self.bot.pg)
        if now.hour == 17 and now.minute == 57:
            await self.close_yesterday_threads()
            await self.post_forums_and_summary()

    async def close_yesterday_threads(self):
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            return
        # Close all threads created yesterday in forums we manage (basic heuristic: archive + lock)
        forums = [
            guild.get_channel(self.bot.forum_common_id),
            guild.get_channel(self.bot.forum_rare_id),
            guild.get_channel(self.bot.forum_sr_id),
            guild.get_channel(self.bot.forum_ssr_id),
            guild.get_channel(self.bot.forum_ur_id),
            guild.get_channel(self.bot.forum_cm_id),
        ]
        cutoff = datetime.now(CEST) - timedelta(days=1)
        for forum in forums:
            if not forum or forum.type != discord.ChannelType.forum:
                continue
            async for thread in forum.threads:
                # Note: forum.threads may be cached; for real-world use, fetch active/archived.
                try:
                    created = thread.created_at or datetime.now(CEST)
                    if created.date() <= cutoff.date():
                        await thread.edit(archived=True, locked=True)
                except Exception:
                    pass

    async def post_forums_and_summary(self):
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            return
        bid = await get_or_create_today_batch(self.bot.pg)
        items = await self.bot.pg.fetch("""
            SELECT bi.position, a.id, a.user_id, a.rarity, a.queue_type, a.series, a.version, a.title
            FROM batch_items bi
            JOIN auctions a ON a.id=bi.auction_id
            WHERE bi.batch_id=$1
            ORDER BY bi.position ASC
        """, bid)
        if not items:
            return

        posted_links = []
        for it in items:
            forum_id = rarity_to_forum_id(self.bot, it["rarity"], it["queue_type"])
            forum = guild.get_channel(forum_id)
            if not forum or forum.type != discord.ChannelType.forum:
                continue
            card_name = it["title"] or (f"{it['series']} v{it['version']}" if it["series"] and it["version"] else f"Auction #{it['id']}")
            initial_msg = f"Seller: <@{it['user_id']}> — Queue: {it['queue_type']}"
            try:
                thread = await forum.create_thread(name=card_name, content=initial_msg)
                link = f"https://discord.com/channels/{guild.id}/{thread.id}"
                posted_links.append((card_name, link))
                await self.bot.pg.execute("UPDATE auctions SET status='POSTED' WHERE id=$1", it["id"])
            except Exception as e:
                print("Error creating thread:", e)

        ping_channel = guild.get_channel(self.bot.ping_channel_id)
        if posted_links and ping_channel:
            lines = [f"[{title}]({link})" for title, link in posted_links]
            await ping_channel.send("Today's auctions:\n" + "\n".join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
