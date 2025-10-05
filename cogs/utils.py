import json
import discord
from discord.ext import commands

def redis_json_load(s: str) -> dict:
    try:
        return json.loads(s)
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
    return mapping.get((rarity or "COMMON").upper(), bot.forum_common_id)

def queue_display_to_type(display: str) -> str:
    return "CARD_MAKER" if display == "Card Maker" else ("SKIP" if display == "Skip queue" else "NORMAL")

def type_to_queue_channel_id(bot: commands.Bot, qtype: str) -> int:
    return bot.queue_cm_id if qtype == "CARD_MAKER" else (bot.queue_skip_id if qtype == "SKIP" else bot.queue_normal_id)
