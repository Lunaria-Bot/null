# cogs/scheduler.py
import asyncio
import datetime
import discord
from discord.ext import commands
from zoneinfo import ZoneInfo
from config.settings import (
    TIMEZONE,
    DAILY_CUTOFF_HOUR, DAILY_CUTOFF_MINUTE,
    CLOSE_FORUMS_HOUR, CLOSE_FORUMS_MINUTE,
    FORUM_COMMON_PREV, FORUM_RARE_PREV, FORUM_SR_PREV, FORUM_SSR_PREV, FORUM_UR_PREV, FORUM_CM_PREV
)
from cogs.batch_preparation import BatchPreparation

class Scheduler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.task = bot.loop.create_task(self._run())

    def cog_unload(self):
        if not self.task.done():
            self.task.cancel()

    async def _run(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                now = datetime.datetime.now(TIMEZONE)

                # 17:30 CEST — close batch intake
                if now.hour == DAILY_CUTOFF_HOUR and now.minute == DAILY_CUTOFF_MINUTE:
                    # Optionnel: verrouiller le batch
                    await self.bot.pg.execute("""
                        UPDATE batches SET status='Closed' WHERE date=$1
                    """, now.date())

                # 17:57 CEST — close previous day forums + post today's cards
                if now.hour == CLOSE_FORUMS_HOUR and now.minute == CLOSE_FORUMS_MINUTE:
                    await self._close_previous_forum_posts()
                    await self._post_today_cards()
                    await self._summarize_today()
                    # Sleep to avoid duplicate within same minute
                    await asyncio.sleep(60)

                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(5)

    async def _close_previous_forum_posts(self):
        guilds = self.bot.guilds
        targets = [FORUM_COMMON_PREV, FORUM_RARE_PREV, FORUM_SR_PREV, FORUM_SSR_PREV, FORUM_UR_PREV, FORUM_CM_PREV]
        for fid in targets:
            forum = self.bot.get_channel(fid)
            if forum and isinstance(forum, discord.ForumChannel):
                async for thread in forum.threads:
                    try:
                        await thread.edit(archived=True, locked=True)
                    except Exception:
                        pass

    async def _post_today_cards(self):
        bp = self.bot.get_cog("BatchPreparation")
        if not isinstance(bp, BatchPreparation):
            return
        # Post all accepted submissions into their rarity forums
        rows = await self.bot.pg.fetch("SELECT id FROM submissions WHERE status='Accepted' AND forum_thread_id IS NULL")
        for r in rows:
            await bp.post_card_thread(r["id"])

    async def _summarize_today(self):
        bp = self.bot.get_cog("BatchPreparation")
        if not isinstance(bp, BatchPreparation):
            return
        await bp.summarize_posts()
