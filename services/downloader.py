import asyncio
import logging
import os
import re
import time

import yt_dlp

from config import (
    DOWNLOADS_DIR,
    CACHE_MAX_AGE_HOURS,
    MAX_CONCURRENT_DOWNLOADS,
    MAX_PLAYLIST_ITEMS,
)
from core.clients import bot

logger = logging.getLogger("music-bot")

audio_cache: dict[str, dict] = {}      # video_id -> {"title", "duration", "path", "thumbnail"}
query_index: dict[str, str] = {}       # normalized query -> video_id
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)


def is_url(text: str) -> bool:
    return bool(re.match(r"https?://", text.strip()))


def _cookies_path() -> str | None:
    path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, "cookies.txt")
    )
    return path if os.path.isfile(path) else None


def _base_opts(playlist: bool = False) -> dict:
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "outtmpl": os.path.join(DOWNLOADS_DIR, "%(id)s.%(ext)s"),
        "noplaylist": not playlist,
        "geo_bypass": True,
        "nocheckcertificate": True,
    }
    cookies = _cookies_path()
    if cookies:
        opts["cookiefile"] = cookies
    return opts


async def fetch_audio(query: str) -> dict:
    """تحميل/حل الصوت عبر yt-dlp. يرجع dict فيه title/duration/path."""
    norm = query.strip().lower()

    cached_id = query_index.get(norm)
    if cached_id and cached_id in audio_cache:
        entry = audio_cache[cached_id]
        if os.path.isfile(entry["path"]):
            return {k: entry.get(k) for k in ("title", "duration", "path", "thumbnail")}

    base_opts = _base_opts(playlist=False)
    if is_url(query):
        target = query
        if "youtube.com" in query or "youtu.be" in query:
            base_opts["extractor_args"] = {
                "youtube": {"player_client": ["android_vr", "web"]}
            }
    else:
        target = f"scsearch1:{query}"

    loop = asyncio.get_event_loop()

    def _run(opts, t):
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(t, download=True)
            if "entries" in info:
                info = info["entries"][0]
            file_path = ydl.prepare_filename(info)
            return {
                "id": info["id"],
                "title": info.get("title", "Unknown"),
                "duration": info.get("duration", 0),
                "path": file_path,
                "thumbnail": info.get("thumbnail"),
            }

    def _cache_and_return(result: dict) -> dict:
        vid = result["id"]
        entry = {
            "title": result["title"],
            "duration": result["duration"],
            "path": result["path"],
            "thumbnail": result.get("thumbnail"),
        }
        audio_cache[vid] = entry
        query_index[norm] = vid
        return entry

    async with DOWNLOAD_SEMAPHORE:
        try:
            return _cache_and_return(
                await loop.run_in_executor(None, _run, base_opts, target)
            )
        except Exception as primary_err:
            if is_url(query) and ("youtube" in query):
                fallback_query = f"scsearch1:{query}"
                try:
                    return _cache_and_return(
                        await loop.run_in_executor(
                            None, _run, base_opts, fallback_query
                        )
                    )
                except Exception:
                    pass
            raise primary_err


async def search_audio(query: str, count: int) -> list[dict]:
    """بحث SoundCloud بدون تحميل. يرجع قائمة نتائج: id/title/duration/url."""
    loop = asyncio.get_event_loop()

    def _search():
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "default_search": "scsearch",
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"scsearch{count}:{query}", download=False)
        entries = info.get("entries", []) if info else []
        results = []
        for e in entries:
            if not e:
                continue
            results.append(
                {
                    "id": e.get("id"),
                    "title": e.get("title", "Unknown"),
                    "duration": e.get("duration", 0),
                    "url": e.get("webpage_url") or e.get("url"),
                    "thumbnail": e.get("thumbnail"),
                }
            )
        return results

    return await loop.run_in_executor(None, _search)


async def fetch_playlist(url: str, max_items: int = MAX_PLAYLIST_ITEMS) -> list[dict]:
    """استخراج عناصر قائمة تشغيل (بدون تحميل) حتى حد أقصى. يرجع url/title/duration."""
    loop = asyncio.get_event_loop()

    def _extract():
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": False,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "extract_flat": True,
            "playlistend": max_items,
        }
        cookies = _cookies_path()
        if cookies:
            opts["cookiefile"] = cookies
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = info.get("entries", []) if info else []
        items = []
        for e in entries[:max_items]:
            if not e:
                continue
            items.append(
                {
                    "url": e.get("url") or e.get("webpage_url"),
                    "title": e.get("title", "Unknown"),
                    "duration": e.get("duration", 0),
                    "thumbnail": e.get("thumbnail"),
                }
            )
        return items

    return await loop.run_in_executor(None, _extract)


def _cached_paths() -> set[str]:
    return {entry["path"] for entry in audio_cache.values()}


async def fetch_from_telegram(message) -> dict | None:
    """تحميل ملف صوتي/فيديو مُرفق برد على رسالة. يرجع None إن لم يوجد ملف مدعوم."""
    replied = getattr(message, "reply_to_message", None)
    if not replied:
        return None

    # تحديد نوع الملف والبيانات الوصفية
    media = None
    unique_id = None
    duration = 0
    title = None
    ext = ".mp3"  # افتراضي للصوت

    if replied.audio:
        m = replied.audio
        media = m
        unique_id = m.file_unique_id
        duration = m.duration or 0
        title = m.title or m.file_name or None
        orig_ext = os.path.splitext(m.file_name or "")[1]
        ext = orig_ext if orig_ext else ".mp3"

    elif replied.voice:
        m = replied.voice
        media = m
        unique_id = m.file_unique_id
        duration = m.duration or 0
        title = None
        ext = ".ogg"

    elif replied.video:
        m = replied.video
        media = m
        unique_id = m.file_unique_id
        duration = m.duration or 0
        title = m.file_name or None
        orig_ext = os.path.splitext(m.file_name or "")[1]
        ext = orig_ext if orig_ext else ".mp4"

    elif replied.video_note:
        m = replied.video_note
        media = m
        unique_id = m.file_unique_id
        duration = m.duration or 0
        title = None
        ext = ".mp4"

    elif replied.document:
        doc = replied.document
        mime = doc.mime_type or ""
        if mime.startswith("audio/") or mime.startswith("video/"):
            media = doc
            unique_id = doc.file_unique_id
            duration = 0
            title = doc.file_name or None
            orig_ext = os.path.splitext(doc.file_name or "")[1]
            if orig_ext:
                ext = orig_ext
            elif mime.startswith("video/"):
                ext = ".mp4"
            else:
                ext = ".mp3"

    if media is None:
        return None

    # ضبط العنوان: title من الملف → caption الرسالة الأصلية → افتراضي
    if not title:
        title = (replied.caption or "").strip() or "ملف تيليجرام"

    # مسار التحميل
    dest = os.path.join(DOWNLOADS_DIR, f"tg_{unique_id}{ext}")

    # تحميل الملف (إن لم يكن محمَّلاً مسبقاً)
    if not os.path.isfile(dest):
        await bot.download_media(replied, file_name=dest)

    return {
        "title": title,
        "duration": duration,
        "path": dest,
        "thumbnail": None,
    }


async def cleanup_downloads_loop():
    """تنظيف دوري لمجلد downloads كل ساعة."""
    while True:
        await asyncio.sleep(3600)
        try:
            max_age_seconds = CACHE_MAX_AGE_HOURS * 3600
            now = time.time()
            active_paths = _cached_paths()
            for name in os.listdir(DOWNLOADS_DIR):
                file_path = os.path.join(DOWNLOADS_DIR, name)
                if not os.path.isfile(file_path):
                    continue
                if file_path in active_paths:
                    continue
                try:
                    age = now - os.path.getmtime(file_path)
                except OSError:
                    continue
                if age > max_age_seconds:
                    try:
                        os.remove(file_path)
                        logger.info(f"cleanup: removed old file {name}")
                    except OSError:
                        logger.warning(f"cleanup: failed to remove {name}")
        except Exception:
            logger.exception("cleanup_downloads_loop error")
