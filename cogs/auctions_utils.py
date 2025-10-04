import re
from datetime import datetime, timedelta, timezone

# Rarity → channel mapping
RARITY_CHANNELS = {
    1342202221558763571: 1304507540645740666,  # Common
    1342202219574857788: 1304507516423766098,  # Rare
    1342202597389373530: 1304536219677626442,  # SR
    1342202212948115510: 1304502617472503908,  # SSR
    1342202203515125801: 1304052056109350922,  # UR
}

# Card Maker Queue target channel
CARDMAKER_CHANNEL_ID = 1395405043431116871

# Release schedule (UTC)
RELEASE_HOUR_UTC = 21
RELEASE_MINUTE_UTC = 57

EMOJI_RE = re.compile(r"<a?:\w+:\d+>")

def strip_discord_emojis(text: str) -> str:
    return EMOJI_RE.sub("", text).strip()

def extract_first_emoji_id(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"<a?:\w+:(\d+)>", text)
    return int(m.group(1)) if m else None

def next_daily_release(now_utc: datetime) -> datetime:
    target = now_utc.replace(hour=RELEASE_HOUR_UTC, minute=RELEASE_MINUTE_UTC, second=0, microsecond=0)
    if target <= now_utc:
        target += timedelta(days=1)
    return target
