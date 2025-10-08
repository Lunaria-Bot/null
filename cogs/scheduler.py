import discord
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from .auction_core import lock_today_batch, get_or_create_today_batch
from .utils import rarity_to_forum_id

CEST = ZoneInfo("Europe/Paris")

# Custom rarity emojis
RARITY_EMOJIS = {
    "COMMON": "<a:Common:1342208021853634781>",
    "RARE": "<a:Rare:1342208028342091857>",
    "SR": "<a:SuperRare:1342208034482425936>",
    "SSR": "<a:SuperSuperRare:1342208039918370857>",
    "UR": "<a:UltraRare:1342208044351623199>",
}

PING_ROLE_ID = 1303005123622207559


async def post_ping_message(channel: discord.TextChannel, daily_index: int, auctions: list):
    """
    Envoie un message formaté dans Auction Ping regroupant les cartes par rareté.
    auctions = liste de dicts avec : id, title, version, event, rarity, link
    """
    lines = [f"<@&{PING_ROLE_ID}> Batch #{daily_index}"]

    # Regrouper par rareté
    grouped = {r: [] for r in RARITY_EMOJIS.keys()}
    for auc in auctions:
        rarity = (auc.get("rarity") or "COMMON").upper()
        if rarity not in grouped:
            grouped[rarity] = []
        event_icon = auc.get("event") or ""
        version = auc.get("version") or "?"
        card_line = f"- [{auc['title']} v{version} {event_icon}]({auc['link']})"
        grouped[rarity].append(card_line)

    # Construire le message uniquement avec les raretés présentes
    for rarity, cards in grouped.items():
        if not cards:
            continue
        emoji = RARITY_EMOJIS.get(rarity, "")
        lines.append(f"\n**{rarity}** {emoji}")
        lines.extend(cards)

    await channel.send("\n".join(lines))


class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    @tasks.loop(minutes=1)
    async def tick(self):
        now = datetime.now(CEST)
        # Lock the batch at 17:30 CEST
        if now.hour == 17 and now.minute == 30:
            await lock_today_batch(self.bot.pg)
        # Close yesterday's threads and post today's auctions at 17:57 CEST
        if now.hour == 17 and now.minute == 57:
            await self.close_yesterday_threads()
            await self.post_forums_and_summary()

    async def close_yesterday_threads(self):
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            return
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
            try:
                threads = forum.threads + (await forum.archived_threads(limit=50)).threads
                for thread in threads:
                    created = thread.created_at or datetime.now(CEST)
                    if created.date() <= cutoff.date():
                        await thread.edit(archived=True, locked=True)
            except Exception as e:
                print("Error closing threads:", e)
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

            # Nom de la carte (sans version pour éviter doublons)
            card_name = it["title"] or (
                it["series"] if it["series"] else f"Auction #{it['id']}"
            )
            rarity = (it.get("rarity") or "COMMON").upper()
            emoji = RARITY_EMOJIS.get(rarity, "")

            # Embed visuel pour le thread
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

                # Marquer comme posté
                await self.bot.pg.execute("UPDATE auctions SET status='POSTED' WHERE id=$1", it["id"])

                # Log interne
                log_channel = guild.get_channel(self.bot.log_channel_id)
                if log_channel:
                    log_embed = discord.Embed(
                        title="Auction posted",
                        color=discord.Color.blue()
                    )
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

                # Ajouter à la liste pour Auction Ping
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

        # Supprimer le batch après publication
        await self.bot.pg.execute("DELETE FROM batches WHERE id=$1", bid)

        # Envoi regroupé dans Auction Ping
        ping_channel = guild.get_channel(self.bot.ping_channel_id)
        if ping_channel and auctions_today:
            await post_ping_message(ping_channel, daily_index, auctions_today)

    # --- Debug command to force posting ---
    @discord.app_commands.command(name="batch-post", description="Force posting of today's batch (debug).")
    @discord.app_commands.default_permissions(administrator=True)
    async def batch_post(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.post_forums_and_summary()
        await interaction.followup.send("✅ Batch posting forced.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
