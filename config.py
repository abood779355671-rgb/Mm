import os

from dotenv import load_dotenv

# يقرأ القيم من ملف .env عند التشغيل المحلي. على Render هذه السطر لا يفعل شيئاً
# لأن المتغيرات يتم حقنها مباشرة من إعدادات الخدمة (Environment Variables).
load_dotenv()

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

for _name, _val in (
    ("API_ID", API_ID),
    ("API_HASH", API_HASH),
    ("BOT_TOKEN", BOT_TOKEN),
    ("SESSION_STRING", SESSION_STRING),
):
    if not _val:
        raise SystemExit(
            f"المتغير {_name} غير موجود. عرّفه في ملف .env محلياً أو في "
            f"Environment Variables على Render."
        )

DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# --- إعدادات الكاش والتحميل (اختيارية، قابلة للضبط من .env) -----------------
# مدة بقاء ملفات downloads قبل اعتبارها قديمة وقابلة للحذف (بالساعات)
CACHE_MAX_AGE_HOURS = int(os.environ.get("CACHE_MAX_AGE_HOURS", "24"))
# الحد الأقصى للتحميلات الفعلية المتزامنة عبر yt-dlp
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "3"))
