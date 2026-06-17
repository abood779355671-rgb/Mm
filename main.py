import asyncio
import logging
import os
import re

import yt_dlp
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import Message
from pytgcalls import PyTgCalls, idle
from pytgcalls.types import MediaStream
from pytgcalls.exceptions import NoActiveGroupCall

from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    SESSION_STRING,
    DOWNLOADS_DIR,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("music-bot")

if not SESSION_STRING:
    raise SystemExit(
        "SESSION_STRING is required - userbot account is needed to join voice chats."
    )

# Ensure all clients share the SAME event loop. Kurigram captures
# asyncio.get_event_loop() at Client() construction; we must set our loop first.
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

# --- Clients -----------------------------------------------------------------
bot = Client(
    name="music_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

userbot = Client(
    name="userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True,
)

call_py = PyTgCalls(userbot)

# --- State -------------------------------------------------------------------
queues: dict[int, list[dict]] = {}
active_chats: dict[int, bool] = {}


# --- Helpers -----------------------------------------------------------------
def is_url(text: str) -> bool:
    return bool(re.match(r"https?://", text.strip()))


async def fetch_audio(query: str) -> dict:
    """Download or resolve audio via yt-dlp. Returns dict with title/duration/path.

    Strategy:
    1. If query is a URL (any site supported by yt-dlp) → use it directly.
       For YouTube URLs, use cookies.txt if present (YouTube blocks datacenter IPs).
    2. Otherwise → search SoundCloud (works without cookies on servers).
    """
    cookies_path = os.path.join(os.path.dirname(__file__), "cookies.txt")
    has_cookies = os.path.isfile(cookies_path)

    base_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "outtmpl": os.path.join(DOWNLOADS_DIR, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }
    if has_cookies:
        base_opts["cookiefile"] = cookies_path

    if is_url(query):
        target = query
        if "youtube.com" in query or "youtu.be" in query:
            base_opts["extractor_args"] = {
                "youtube": {"player_client": ["android_vr", "web"]}
            }
    else:
        # Search SoundCloud by default (YouTube datacenter blocking)
        target = f"scsearch1:{query}"

    loop = asyncio.get_event_loop()

    def _run(opts, t):
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(t, download=True)
            if "entries" in info:
                info = info["entries"][0]
            file_path = os.path.join(DOWNLOADS_DIR, f"{info['id']}.mp3")
            return {
                "title": info.get("title", "Unknown"),
                "duration": info.get("duration", 0),
                "path": file_path,
            }

    try:
        return await loop.run_in_executor(None, _run, base_opts, target)
    except Exception as primary_err:
        if is_url(query) and ("youtube" in query):
            fallback_query = f"scsearch1:{query}"
            try:
                return await loop.run_in_executor(None, _run, base_opts, fallback_query)
            except Exception:
                pass
        raise primary_err


async def play_song(chat_id: int, song: dict, message: Message):
    try:
        await call_py.play(chat_id, MediaStream(song["path"]))
        active_chats[chat_id] = True
        await message.reply(
            f"🎵 **يتم التشغيل الآن:**\n`{song['title']}`\n⏱ **المدة:** {song['duration']} ثانية"
        )
    except NoActiveGroupCall:
        await message.reply(
            "❌ **لا توجد مكالمة جماعية نشطة!** ابدأ Voice Chat في المجموعة أولاً ثم أعد المحاولة."
        )
        queues.get(chat_id, []).clear()
    except Exception as e:
        logger.exception("play error")
        await message.reply(f"❌ **خطأ أثناء التشغيل:** `{e}`")
        if chat_id in queues and queues[chat_id]:
            queues[chat_id].pop(0)


async def play_next(chat_id: int, message: Message):
    if not queues.get(chat_id):
        active_chats.pop(chat_id, None)
        try:
            await call_py.leave_call(chat_id)
        except Exception:
            pass
        return
    song = queues[chat_id][0]
    await play_song(chat_id, song, message)


# --- Bot commands ------------------------------------------------------------
HELP_TEXT = (
    "🎵 **بوت الموسيقى — جاهز للعمل!**\n\n"
    "**الأوامر العربية (بدون شرطة):**\n"
    "• `تشغيل <اسم الأغنية أو رابط>` — تشغيل أغنية\n"
    "• `ايقاف مؤقت` — إيقاف مؤقت\n"
    "• `استكمال` — متابعة التشغيل\n"
    "• `تخطي` — تخطي الأغنية الحالية\n"
    "• `ايقاف` — إنهاء التشغيل ومسح القائمة\n"
    "• `القائمة` — عرض قائمة الانتظار\n"
    "• `بنق` — اختبار اتصال البوت\n\n"
    "**كذلك تعمل أوامر السلاش الإنجليزية:**\n"
    "`/play`, `/pause`, `/resume`, `/skip`, `/stop`, `/queue`, `/ping`\n\n"
    "**ملاحظة:** ابدأ المكالمة الجماعية في المجموعة قبل التشغيل."
)

RX_START = r"^(?:بدء|البدء|ابدأ|إبدأ|بداية)$"
RX_PING = r"^(?:بنق|بينق|ping)$"
RX_PLAY = r"^(?:تشغيل|شغل|شغّل)\s+(.+)$"
RX_PAUSE = r"^(?:ايقاف\s*مؤقت|إيقاف\s*مؤقت|توقف\s*مؤقت|توقّف\s*مؤقت)$"
RX_RESUME = r"^(?:استكمال|إستكمال|متابعة|اكمال|إكمال|كمل)$"
RX_SKIP = r"^(?:تخطي|تخطى|تخطّي|التالي|تالي)$"
RX_STOP = r"^(?:ايقاف|إيقاف|توقف|توقّف|انهاء|إنهاء|اوقف|أوقف)$"
RX_QUEUE = r"^(?:القائمة|قائمة|قائمه|القائمه|الطابور|طابور)$"


@bot.on_message(filters.command("start") | filters.regex(RX_START))
async def start_cmd(_, message: Message):
    await message.reply_text(HELP_TEXT)


@bot.on_message(filters.command("ping") | filters.regex(RX_PING))
async def ping_cmd(_, message: Message):
    await message.reply("🏓 **بونق!** البوت يعمل بشكل ممتاز.")


async def _do_play(message: Message, query: str):
    chat_id = message.chat.id
    status = await message.reply("🔍 **جاري البحث والتحميل...**")
    try:
        song = await fetch_audio(query)
    except Exception as e:
        await status.edit(f"❌ **فشل في جلب الصوت:** `{e}`")
        return

    queues.setdefault(chat_id, []).append(song)

    if len(queues[chat_id]) == 1 and not active_chats.get(chat_id):
        await status.delete()
        await play_song(chat_id, song, message)
    else:
        await status.edit(
            f"✅ **تمت إضافتها للقائمة:** `{song['title']}`\n"
            f"**الترتيب:** {len(queues[chat_id])}"
        )


@bot.on_message(filters.command("play") & filters.group)
async def play_cmd(_, message: Message):
    if len(message.command) < 2 and not (
        message.reply_to_message and message.reply_to_message.text
    ):
        await message.reply(
            "❌ **اكتب اسم أغنية أو ضع رابطاً!**\n"
            "مثال: `تشغيل ديسباسيتو` أو `/play despacito`"
        )
        return
    if message.reply_to_message and message.reply_to_message.text:
        query = message.reply_to_message.text
    else:
        query = message.text.split(maxsplit=1)[1]
    await _do_play(message, query)


@bot.on_message(filters.regex(RX_PLAY) & filters.group)
async def play_arabic_cmd(_, message: Message):
    m = re.match(RX_PLAY, message.text or "")
    if not m:
        return
    query = m.group(1).strip()
    if not query:
        await message.reply("❌ **اكتب اسم أغنية بعد كلمة 'تشغيل'.**")
        return
    await _do_play(message, query)


@bot.on_message((filters.command("pause") | filters.regex(RX_PAUSE)) & filters.group)
async def pause_cmd(_, message: Message):
    chat_id = message.chat.id
    if active_chats.get(chat_id):
        await call_py.pause_stream(chat_id)
        await message.reply("⏸ **تم إيقاف الموسيقى مؤقتاً.**")
    else:
        await message.reply("❌ **لا توجد موسيقى قيد التشغيل!**")


@bot.on_message((filters.command("resume") | filters.regex(RX_RESUME)) & filters.group)
async def resume_cmd(_, message: Message):
    chat_id = message.chat.id
    if active_chats.get(chat_id):
        await call_py.resume_stream(chat_id)
        await message.reply("▶ **تمت متابعة التشغيل.**")
    else:
        await message.reply("❌ **لا توجد موسيقى قيد التشغيل!**")


@bot.on_message((filters.command("skip") | filters.regex(RX_SKIP)) & filters.group)
async def skip_cmd(_, message: Message):
    chat_id = message.chat.id
    if not queues.get(chat_id):
        await message.reply("❌ **القائمة فارغة!**")
        return
    queues[chat_id].pop(0)
    if queues[chat_id]:
        await play_next(chat_id, message)
        await message.reply("⏭ **تم الانتقال للأغنية التالية.**")
    else:
        try:
            await call_py.leave_call(chat_id)
        except Exception:
            pass
        active_chats.pop(chat_id, None)
        await message.reply("⏭ **انتهت القائمة.**")


@bot.on_message((filters.command("stop") | filters.regex(RX_STOP)) & filters.group)
async def stop_cmd(_, message: Message):
    chat_id = message.chat.id
    queues.pop(chat_id, None)
    active_chats.pop(chat_id, None)
    try:
        await call_py.leave_call(chat_id)
        await message.reply("⏹ **تم إيقاف الموسيقى ومسح القائمة.**")
    except Exception:
        await message.reply("❌ **لا يوجد شيء لإيقافه.**")


@bot.on_message((filters.command("queue") | filters.regex(RX_QUEUE)) & filters.group)
async def queue_cmd(_, message: Message):
    chat_id = message.chat.id
    if not queues.get(chat_id):
        await message.reply("📭 **القائمة فارغة!**")
        return
    text = "🎶 **قائمة الانتظار:**\n\n"
    for i, song in enumerate(queues[chat_id], 1):
        title = song["title"][:60]
        text += f"{i}. `{title}`\n"
    text += f"\n**المجموع:** {len(queues[chat_id])} أغنية"
    await message.reply(text)


# --- PyTgCalls events --------------------------------------------------------
@call_py.on_update()
async def on_call_update(_, update):
    try:
        from pytgcalls.types import StreamEnded

        if isinstance(update, StreamEnded):
            chat_id = update.chat_id
            if queues.get(chat_id):
                queues[chat_id].pop(0)
            if queues.get(chat_id):
                song = queues[chat_id][0]
                await call_py.play(chat_id, MediaStream(song["path"]))
            else:
                active_chats.pop(chat_id, None)
                try:
                    await call_py.leave_call(chat_id)
                except Exception:
                    pass
    except Exception:
        logger.exception("on_call_update error")


# --- Web server (Render يطلب فتح بورت حتى تُعتبر الخدمة شغالة) -------------
async def handle_health(request):
    return web.Response(text="Music bot is running.")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server listening on 0.0.0.0:{port}")


# --- Main --------------------------------------------------------------------
async def main():
    await bot.start()
    await call_py.start()
    await run_web_server()
    me = await bot.get_me()
    logger.info(f"Bot started as @{me.username}")
    print(f"Bot started as @{me.username}", flush=True)
    await idle()
    await bot.stop()


if __name__ == "__main__":
    try:
        LOOP.run_until_complete(main())
    finally:
        LOOP.close()
