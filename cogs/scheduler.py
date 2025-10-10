import discord
from discord.ext import commands
from .auction_core import get_or_create_today_batch
from .utils import rarity_to_forum_id
from .admin_guard import is_staff
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
import re

CEST = ZoneInfo("Europe/Paris")

RARITY_EMOJIS = {
    "COMMON": "<a:Common:1342208021853634781>",
    "RARE": "<a:Rare:1342208028342091857>",
    "SR": "<a:SuperRare:1342208034482425936>",
    "SSR": "<a:SuperSuperRare:1342208039918370857>",
    "UR": "<a:UltraRare:1342208044351623199>",
}

PING_ROLE_ID = 1303005123622207559
VERSION_SUFFIX_RE = re.compile(r"\s*v\s*\d+\s*$", re.IGNORECASE)

def strip_version_suffix(name: str) -> str:
    if not name:
        return name
    return VERSION_SUFFIX_RE.sub("", name).strip()

async def post_ping_message(channel: discord.TextChannel, daily_index: int, auctions: list):
    lines = [f"<@&{PING_ROLE_ID}> Batch #{daily_index}"]

    grouped = {r: [] for r in RARITY_EMOJIS.keys()}
    for auc in auctions:
        rarity = (auc.get("rarity") or "COMMON").upper()
        if rarity not in grouped:
            grouped[rarity] = []
        version = auc.get("version")
        event_icon = auc.get("event") or ""
        display_title = auc["title"]
        if version:
            card_line = f"[{display_title}]({auc['link']}) `v{version}` {event_icon}".strip()
        else:
            card_line = f"[{display_title}]({auc['link']}) {event_icon}".strip()
        grouped[rarity].append(card_line)

    for rarity, cards in grouped.items():
        if not cards:
            continue
        emoji = RARITY_EMOJIS.get(rarity, "")
        lines.append(f"\n# {emoji}")
        lines.extend(cards)

    content = "\n".join(lines)
    for chunk in [content[i:i+3900] for i in range(0, len(content), 3900)]:
        await channel.send(chunk)

class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def post_forums_and_summary(self):
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            return

        bid = await get_or_create_today_batch(self.bot.pg)
        items = await self.bot.pg.fetch("""
            SELECT bi.position, a.id, a.user_id, a.rarity, a.queue_type,
                   a.series, a.version, a.title, a.currency, a.rate, a.image_url, a.event
            FROM batch_items bi
            JOIN auctions a ON a.id=bi.auction_id
            WHERE bi.batch_id=$1
            ORDER BY bi.position ASC
        """, bid)
        if not items:
            return

        auctions_today = []
        daily_index = 0

        for it in items:
            daily_index += 1
            forum_id = rarity_to_forum_id(self.bot, it["rarity"], it["queue_type"])
            forum = guild.get_channel(forum_id)
            if not forum or forum.type != discord.ChannelType.forum:
                continue

            raw_name = it["title"] or (it["series"] if it["series"] else f"Auction #{it['id']}")
            card_name = strip_version_suffix(raw_name)
            rarity = (it.get("rarity") or "COMMON").upper()
            emoji = RARITY_EMOJIS.get(rarity, "")

            embed = discord.Embed(
                title=f"{emoji} {card_name}" if emoji else card_name,
                description=f"Auction posted by <@{it['user_id']}>",
                color=discord.Color.blurple()
            )
            embed.add_field(name="Seller", value=f"<@{it['user_id']}>", inline=True)
            embed.add_field(name="Rarity", value=f"{emoji} {rarity}" if emoji else (it.get("rarity") or "?"), inline=True)
            embed.add_field(name="Preference", value=it.get("currency") or "N/A", inline=True)
            embed.add_field(name="Rate", value=it.get("rate") or "N/A", inline=True)
            embed.add_field(name="Version", value=it.get("version") or "?", inline=True)
            if it.get("image_url"):
                embed.set_image(url=it["image_url"])

            try:
                thread_with_msg = await forum.create_thread(
                    name=card_name,
                    content=None,
                    embed=embed
                )
                thread = thread_with_msg.thread
                link = f"https://discord.com/channels/{guild.id}/{thread.id}"
                await self.bot.pg.execute("UPDATE auctions SET status='POSTED' WHERE id=$1", it["id"])

                log_channel = guild.get_channel(self.bot.log_channel_id)
                if log_channel:
                    log_embed = discord.Embed(title="Auction posted", color=discord.Color.blue())
                    log_embed.add_field(name="Name of the card", value=(f"{emoji} {card_name}" if emoji else card_name), inline=True)
                    log_embed.add_field(name="Version", value=it.get("version") or "?", inline=True)
                    log_embed.add_field(name="Queue", value=it.get("queue_type") or "?", inline=True)
                    log_embed.add_field(name="Seller", value=f"<@{it['user_id']}>", inline=True)
                    log_embed.add_field(name="Rarity", value=f"{emoji} {rarity}" if emoji else (it.get("rarity") or "?"), inline=True)
                    log_embed.add_field(name="Currency", value=it.get("currency") or "N/A", inline=True)
                    log_embed.add_field(name="Rate", value=it.get("rate") or "N/A", inline=True)
                    if it.get("image_url"):
                        log_embed.set_image(url=it["image_url"])
                    await log_channel.send(embed=log_embed)

                auctions_today.append({
                    "id": it["id"],
                    "title": card_name,
                    "version": it.get("version"),
                    "event": it.get("event"),
                    "rarity": it.get("rarity"),
                    "link": link
                })

            except Exception as e:
                print("Error creating thread:", e)

        await self.bot.pg.execute("DELETE FROM batches WHERE id=$1", bid)

        ping_channel = guild.get_channel(self.bot.ping_channel_id)
        if ping_channel and auctions_today:
            await post_ping_message(ping_channel, daily_index, auctions_today)

    @discord.app_commands.command(name="batch-post", description="Post today auction.")
    @is_staff()
    async def batch_post(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.post_forums_and_summary()
        await interaction.followup.send("✅ Batch posting forced.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
