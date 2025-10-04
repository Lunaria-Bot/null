import re
from datetime import datetime, time, timedelta, timezone

def strip_discord_emojis(text: str) -> str:
    """Supprime les emojis Discord custom d'un texte."""
    return re.sub(r"<a?:\w+:\d+>", "", text)

def extract_first_emoji_id(description: str) -> int | None:
    """Extrait l'ID du premier emoji custom trouvé dans une description."""
    match = re.search(r"<a?:\w+:(\d+)>", description or "")
    return int(match.group(1)) if match else None

def next_daily_release(now: datetime) -> datetime:
    """
    Calcule la prochaine heure de release quotidienne (21h57 UTC).
    Retourne toujours un datetime aware en UTC.
    """
    release_time = time(21, 57, tzinfo=timezone.utc)
    release_at = datetime.combine(now.date(), release_time)

    if release_at.tzinfo is None:
        release_at = release_at.replace(tzinfo=timezone.utc)

    if release_at <= now:
        release_at += timedelta(days=1)

    return release_at

def is_after_cutoff(now: datetime) -> bool:
    """
    Vérifie si l'heure actuelle est après le cutoff (17h30 UTC).
    Retourne True si on est après 17h30 UTC, sinon False.
    """
    cutoff = time(17, 30, tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.time() >= cutoff

# --- Constantes de mapping ---
RARITY_CHANNELS = {
    1342202221558763571: 1304507540645740666,  # Common
    1342202219574857788: 1304507516423766098,  # Rare
    1342202597389373530: 1304536219677626442,  # SR
    1342202212948115510: 1304502617472503908,  # SSR
    1342202203515125801: 1304052056109350922,  # UR
}

CARDMAKER_CHANNEL_ID = 1395405043431116871  # <-- remplace par l'ID réel de ton forum CardMaker
