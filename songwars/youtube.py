"""
Hent et beat fra et YouTube-link. Kun admin bruger det.

Samme opskrift som "YouTube -> WAV"-scriptet: yt-dlp henter kun den ene
video (aldrig en playliste), ffmpeg laver lyden om, og deno er den
JavaScript-motor yt-dlp skal bruge til YouTube. Her bliver det mp3 i
stedet for wav: lyden paa YouTube er allerede komprimeret, saa en wav
bliver ikke bedre - bare fire gange stoerre at hente for spillerne.

Kun YouTube: linket laves om til https://www.youtube.com/watch?v=<id>, og
yt-dlp maa kun bruge sin YouTube-del. Saa kan feltet ikke bruges til at
faa serveren til at hente fra andre adresser.
"""

import re
import secrets
import urllib.parse

HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
         "youtu.be", "www.youtu.be"}
ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


class FetchError(Exception):
    """Noget gik galt. Beskeden kan vises til admin, som den er."""


def video_id(url):
    """Video-id'et fra et YouTube-link, eller None. Godtager watch-, youtu.be-,
    shorts-, live- og embed-links; alt andet afvises."""
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if host not in HOSTS:
        return None
    path = parts.path
    if host.endswith("youtu.be"):
        vid = path.strip("/").split("/")[0]
    elif path == "/watch":
        vid = urllib.parse.parse_qs(parts.query).get("v", [""])[0]
    elif path.startswith(("/shorts/", "/live/", "/embed/")):
        vid = path.split("/")[2] if len(path.split("/")) > 2 else ""
    else:
        return None
    return vid if ID_RE.fullmatch(vid) else None


def watch_url(vid):
    return "https://www.youtube.com/watch?v=" + vid


def clean_title(title):
    """Videoens titel som beattitel: uden "(FREE)", "[free for profit]",
    "Type Beat" og lignende - det der er tilbage, er som regel navnet."""
    title = title or ""
    title = re.sub(r"[\(\[][^\)\]]*(free|prod|profit)[^\)\]]*[\)\]]", " ", title, flags=re.I)
    quoted = re.search(r"[\"'＂“”](.+?)[\"'＂“”]", title)
    if quoted:
        return quoted.group(1).strip()
    title = re.sub(r"\btype\s+beat\b|\binstrumental\b|\b20\d\d\b", " ", title, flags=re.I)
    title = re.sub(r"[\(\[]\s*[\)\]]", " ", title)                  # tomme parenteser
    return re.sub(r"\s+", " ", title).strip(" -|·,") or "Beat fra YouTube"


def explain(message):
    """yt-dlps fejl paa dansk - med hvad man kan goere ved det."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", message or "")
    low = text.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube afviste serveren (bot-tjek). Hent beatet med dit eget værktøj "
                "og læg filen op herunder i stedet.")
    if "private video" in low:
        return "Videoen er privat."
    if ("unavailable" in low or "removed" in low or "does not exist" in low
            or "not available" in low):
        return "Videoen findes ikke, eller den er fjernet."
    if "age" in low and "confirm" in low:
        return "Videoen har aldersgrænse, så serveren kan ikke hente den."
    if "http error 403" in low:
        return ("YouTube svarede 403. yt-dlp skal nok opdateres — byg containeren igen "
                "(se DEPLOY.md).")
    if "ffmpeg" in low and ("not found" in low or "not installed" in low):
        return "ffmpeg mangler på serveren."
    last = [line for line in text.splitlines() if line.strip()]
    detail = (last[-1] if last else text).replace("ERROR:", "").strip()
    return "Hentningen fejlede: " + detail[:200]


def fetch(vid, workdir, max_bytes, max_seconds):
    """Hent videoens lyd som mp3 i `workdir`. Returnerer (sti, videoens titel).
    Rejser FetchError med en dansk besked."""
    try:
        import yt_dlp
    except ImportError:
        raise FetchError("yt-dlp er ikke installeret på serveren.")

    token = secrets.token_hex(16)
    rejected = []

    def check(info, *, incomplete=False):
        duration = info.get("duration")
        if info.get("is_live"):
            rejected.append("Livestreams kan ikke hentes.")
        elif duration and duration > max_seconds:
            rejected.append("Videoen er over {0} minutter.".format(max_seconds // 60))
        return rejected[-1] if rejected else None

    opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "allowed_extractors": ["youtube"],
        "outtmpl": str(workdir / (token + ".%(ext)s")),
        "paths": {"home": str(workdir), "temp": str(workdir)},
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                            "preferredquality": "0"}],
        "max_filesize": max_bytes,
        "match_filter": check,
        "cachedir": False,
        "socket_timeout": 20,
        "retries": 3,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "overwrites": True,
    }

    def leftovers():
        for path in workdir.glob(token + ".*"):
            try:
                path.unlink()
            except OSError:
                pass

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(watch_url(vid), download=True) or {}
    except yt_dlp.utils.DownloadError as exc:
        leftovers()
        raise FetchError(explain(str(exc)))
    except Exception as exc:                          # yt-dlp kan rejse alt muligt
        leftovers()
        raise FetchError(explain(str(exc)))

    if rejected:
        leftovers()
        raise FetchError(rejected[-1])
    mp3 = workdir / (token + ".mp3")
    if not mp3.is_file():
        leftovers()
        raise FetchError("Lyden kunne ikke laves om til mp3. Er filen for stor (over {0} MB)?"
                         .format(max_bytes // (1024 * 1024)))
    for path in workdir.glob(token + ".*"):           # det hentede foer det blev til mp3
        if path != mp3:
            try:
                path.unlink()
            except OSError:
                pass
    return mp3, info.get("title") or ""
