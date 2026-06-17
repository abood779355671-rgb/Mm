import os

from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
# دعم عدة حسابات مساعدة: جلسات مفصولة بفاصلة. fallback إلى SESSION_STRING المفرد.
SESSION_STRINGS = os.environ.get("SESSION_STRINGS", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))


def _resolve_sessions() -> list[str]:
    raw = SESSION_STRINGS.strip() or SESSION_STRING.strip()
    return [s.strip() for s in raw.split(",") if s.strip()]


SESSIONS = _resolve_sessions()

for _name, _val in (
    ("API_ID", API_ID),
    ("API_HASH", API_HASH),
    ("BOT_TOKEN", BOT_TOKEN),
):
    if not _val:
        raise SystemExit(
            f"المتغير {_name} غير موجود. عرّفه في ملف .env محلياً أو في "
            f"Environment Variables على المنصة."
        )

if not SESSIONS:
    raise SystemExit(
        "يجب توفير SESSION_STRING واحد على الأقل (أو SESSION_STRINGS مفصولة بفاصلة)."
    )

DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# --- إعدادات الكاش والتحميل والتوسّع (اختيارية، قابلة للضبط من .env) ---------
CACHE_MAX_AGE_HOURS = int(os.environ.get("CACHE_MAX_AGE_HOURS", "24"))
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "3"))
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
IDLE_TIMEOUT_MINUTES = int(os.environ.get("IDLE_TIMEOUT_MINUTES", "5"))
SEARCH_RESULTS_COUNT = int(os.environ.get("SEARCH_RESULTS_COUNT", "5"))
ADMIN_CACHE_TTL = int(os.environ.get("ADMIN_CACHE_TTL", "60"))
MAX_PLAYLIST_ITEMS = int(os.environ.get("MAX_PLAYLIST_ITEMS", "20"))
