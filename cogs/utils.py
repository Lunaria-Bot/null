import ast
import discord
from discord.ext import commands

def parse_cached_embed_str(s: str) -> dict:
    # Stored via str(data); we convert back safely using ast.literal_eval
    try:
        return ast.literal_eval(s)
    except Exception:
        return {}

def rarity_to_forum_id(bot: commands.Bot, rarity: str, queue_type: str) -> int:
    if queue_type == "CARD_MAKER":
        return bot.forum_cm_id
    mapping = {
        "COMMON": bot.forum_common_id,
        "RARE": bot.forum_rare_id,
        "SR": bot.forum_sr_id,
        "SSR": bot.forum_ssr_id,
        "UR": bot.forum_ur_id,
    }
    return mapping.get(rarity or "COMMON", bot.forum_common_id)

def queue_display_to_type(display: str) -> str:
    if display == "Card Maker":
        return "CARD_MAKER"
    if display == "Skip queue":
        return "SKIP"
    return "NORMAL"

def type_to_queue_channel_id(bot: commands.Bot, qtype: str) -> int:
    if qtype == "CARD_MAKER":
        return bot.queue_cm_id
    if qtype == "SKIP":
        return bot.queue_skip_id
    return bot.queue_normal_id
