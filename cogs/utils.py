# cogs/utils.py
import re
import json
import discord
from discord.ext import commands

class Utils(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def strip_discord_emojis(text: str) -> str:
        return re.sub(r"<a?:\w+:\d+>", "", text).strip()

    @staticmethod
    def parse_owner_id_from_description(desc: str) -> int | None:
        if "Owned by <@" in desc:
            try:
                start = desc.find("Owned by <@") + len("Owned by <@")
                end = desc.find(">", start)
                return int(desc[start:end])
            except Exception:
                return None
        return None

    @staticmethod
    def detect_rarity_from_embed(embed_dict: dict) -> str | None:
        # Try from title or description or footer
        text = " ".join([
            embed_dict.get("title") or "",
            embed_dict.get("description") or "",
            (embed_dict.get("footer") or {}).get("text") or "",
        ]).upper()
        for r in ["COMMON", "RARE", "SR", "SSR", "UR"]:
            if r in text:
                return "SR" if r == "SR" else ("SSR" if r == "SSR" else (r.capitalize()))
        # Try from image card text lines if provided
        return None

    @staticmethod
    def extract_image_url(embed_dict: dict) -> str | None:
        image = embed_dict.get("image")
        if image and image.get("url"):
            return image["url"]
        thumbnail = embed_dict.get("thumbnail")
        return thumbnail["url"] if thumbnail and thumbnail.get("url") else None

    @staticmethod
    def build_card_title(embed_dict: dict) -> str:
        title = embed_dict.get("title") or ""
        return title.strip() if title else "Unknown Card"

    @staticmethod
    def json_dumps(data: dict) -> str:
        return json.dumps(data, ensure_ascii=False)
