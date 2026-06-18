import json
import logging
import re

from pyrogram import filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from pytgcalls.types import MediaStream
from pytgcalls.exceptions import NoActiveGroupCall

from config import SEARCH_RESULTS_COUNT, MAX_PLAYLIST_ITEMS, OWNER_ID
from core.clients import bot, redis_client
from services import queue_manager as qm
from services import assistants
from services import stats
from services.downloader import fetch_audio, search_audio, fetch_playlist, is_url, fetch_from_telegram
from services.telegram_safe import safe_call
from services.permissions import can_control, is_chat_admin

logger = logging.getLogger("music-bot")

GENERIC_ERROR = "❌ **تعذر تشغيل هذا المحتوى، جرّب رابطاً أو اسماً آخر.**"
NO_PERMISSION = "🚫 **هذا الأمر للمشرفين أو لمن بدأ التشغيل فقط.**"

HELP_TEXT = (
    "🎵 **بوت الموسيقى — جاهز للعمل!**\n\n"
    "**الأوامر العربية (بدون شرطة):**\n"
    "• `تشغيل <اسم الأغنية أو رابط>` — تشغيل أغنية أو قائمة\n"
    "• `ايقاف مؤقت` / `استكمال` — تحكم بالتشغيل\n"
    "• `تخطي` / `ايقاف` — تخطي/إنهاء\n"
    "• `القائمة` — عرض قائمة الانتظار\n"
    "• `تكرار` / `عشوائي` — تكرار الأغنية / خلط القائمة\n"
    "• `فيديو` — تبديل صوت فقط/فيديو\n"
    "• `وضع_ادمن [تشغيل|ايقاف]` — تقييد التشغيل للمشرفين فقط (للأدمن)\n"
    "• `ربط_قناة linked` — بث الصوت عبر Voice Chat القناة المرتبطة (للأدمن) — يتطلب Voice Chat نشط في القناة\n"
    "• `بنق` — اختبار الاتصال\n\n"
    "**أوامر السلاش:** `/play`, `/pause`, `/resume`, `/skip`, `/stop`, "
    "`/queue`, `/loop`, `/shuffle`, `/video`, `/ping`, `/adminplay`, `/cplay`\n\n"
    "**أزرار التحكم:** تشمل الآن ⏸/▶️ إيقاف/استكمال، 🔁 تكرار، 🔀 عشوائي — تتحدث فوراً.\n\n"
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
RX_LOOP = r"^(?:تكرار|كرر|repeat|loop)$"
RX_SHUFFLE = r"^(?:عشوائي|عشوائى|خلط|shuffle)$"
RX_VIDEO = r"^(?:فيديو|فيديوهات|video)$"
RX_STATS = r"^(?:احصائيات|إحصائيات|الاحصائيات|stats)$"
RX_ADMINPLAY = r"^(?:وضع[_ ]ادمن|وضع[_ ]أدمن)(?:\s+(.+))?$"
RX_CPLAY = r"^(?:ربط[_ ]قناة|ربط_قناة)(?:\s+(.+))?$"
RX_PLAY_BARE = r"^(?:تشغيل|شغل|شغّل)\s*$"


# --- فحص وجود ملف قابل للتشغيل في الرسالة المردود عليها -------------------
def _has_playable_media(msg) -> bool:
    """يتحقق إن كانت الرسالة تحتوي ملف صوتي/فيديو مدعوم."""
    if not msg:
        return False
    if msg.audio or msg.voice or msg.video or msg.video_note:
        return True
    if msg.document:
        mime = msg.document.mime_type or ""
        return mime.startswith("audio/") or mime.startswith("video/")
    return False


# --- تشغيل ملف مرفق من تيليجرام -------------------------------------------
async def _handle_telegram_file_play(message):
    user_id = message.from_user.id if message.from_user else 0
    status = await safe_call(message.reply, "⬇️ **جاري تحميل الملف من تيليجرام...**")
    try:
        song = await fetch_from_telegram(message)
    except Exception as e:
        logger.exception(e)
        await safe_call(status.edit, GENERIC_ERROR)
        return
    if song is None:
        await safe_call(status.edit, "❌ **لا يوجد ملف صوتي أو فيديو مدعوم في الرسالة.**")
        return
    await safe_call(status.delete)
    song["requester_id"] = user_id
    await _start_or_queue(message, song, user_id)


# --- مساعدات العرض ----------------------------------------------------------
def _fmt_duration(seconds) -> str:
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return "?"
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


def _controls_kb(paused: bool = False) -> InlineKeyboardMarkup:
    # الصف الأول: زر pause/resume ديناميكي + تخطي + إيقاف
    play_pause_btn = (
        InlineKeyboardButton("▶️ استكمال", callback_data="ctl:resume")
        if paused
        else InlineKeyboardButton("⏸ إيقاف مؤقت", callback_data="ctl:pause")
    )
    return InlineKeyboardMarkup(
        [
            [
                play_pause_btn,
                InlineKeyboardButton("⏭", callback_data="ctl:skip"),
                InlineKeyboardButton("⏹", callback_data="ctl:stop"),
            ],
            [InlineKeyboardButton("🎬 صوت/فيديو", callback_data="vid:toggle")],
            [
                InlineKeyboardButton("🔁 تكرار", callback_data="ctl:loop"),
                InlineKeyboardButton("🔀 عشوائي", callback_data="ctl:shuffle"),
            ],
        ]
    )


def _make_stream(path: str, video: bool) -> MediaStream:
    """بناء MediaStream بنمط صوت فقط أو صوت+فيديو."""
    if video:
        return MediaStream(path)
    return MediaStream(path, video_flags=MediaStream.Flags.IGNORE)


# --- التشغيل ----------------------------------------------------------------
async def play_song(chat_id: int, song: dict, message: Message):
    try:
        call_py = await assistants.call_for(chat_id)
        video = await qm.is_video(chat_id)
        target = await qm.target_chat_id(chat_id)
        await safe_call(call_py.play, target, _make_stream(song["path"], video))
        await assistants.incr_load(chat_id)
        await qm.set_active(chat_id, True)
        await qm.set_paused(chat_id, False)  # أغنية جديدة → ليست موقوفة مؤقتاً
        await qm.touch_activity(chat_id)
        await stats.record_play(song.get("title", "Unknown"))
        text = (
            f"🎵 **يتم التشغيل الآن:**\n`{song['title']}`\n"
            f"⏱ **المدة:** {_fmt_duration(song['duration'])}"
            + ("  •  🎬 فيديو" if video else "")
        )
        thumb = song.get("thumbnail")
        if thumb:
            try:
                await safe_call(
                    message.reply_photo,
                    photo=thumb,
                    caption=text,
                    reply_markup=_controls_kb(paused=False),
                )
            except Exception:
                await safe_call(message.reply, text, reply_markup=_controls_kb(paused=False))
        else:
            await safe_call(message.reply, text, reply_markup=_controls_kb(paused=False))
    except NoActiveGroupCall:
        await safe_call(
            message.reply,
            "❌ **لا توجد مكالمة جماعية نشطة!** ابدأ Voice Chat في المجموعة أولاً ثم أعد المحاولة.",
        )
        await qm.clear_queue(chat_id)
    except Exception as e:
        logger.exception(e)
        await safe_call(message.reply, GENERIC_ERROR)
        await qm.pop_song(chat_id)


async def _leave(chat_id: int):
    """مغادرة المكالمة عبر الحساب المساعد المسؤول + تحديث العدّاد."""
    try:
        call_py = await assistants.call_for(chat_id)
        target = await qm.target_chat_id(chat_id)
        await safe_call(call_py.leave_call, target)
    except Exception:
        pass
    await assistants.decr_load(chat_id)


async def play_next(chat_id: int, message: Message):
    song = await qm.peek_song(chat_id)
    if song is None:
        await qm.set_active(chat_id, False)
        await _leave(chat_id)
        return
    await play_song(chat_id, song, message)


# --- البحث والاختيار --------------------------------------------------------
def _results_kb(results: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i, r in enumerate(results):
        label = f"{i + 1}. {r['title'][:40]} [{_fmt_duration(r['duration'])}]"
        rows.append([InlineKeyboardButton(label, callback_data=f"sel:{i}")])
    return InlineKeyboardMarkup(rows)


async def _do_search(message: Message, query: str):
    chat_id = message.chat.id
    status = await safe_call(message.reply, "🔍 **جاري البحث...**")
    try:
        results = await search_audio(query, SEARCH_RESULTS_COUNT)
    except Exception as e:
        logger.exception(e)
        await safe_call(status.edit, GENERIC_ERROR)
        return
    if not results:
        await safe_call(status.edit, "❌ **لا توجد نتائج، جرّب اسماً آخر.**")
        return
    key = f"search:{chat_id}:{status.id}"
    await redis_client.set(key, json.dumps(results), ex=300)
    await safe_call(status.edit, "🎼 **اختر نتيجة:**", reply_markup=_results_kb(results))


async def _start_or_queue(message: Message, song: dict, user_id: int):
    chat_id = message.chat.id
    new_len = await qm.push_song(chat_id, song)
    await qm.touch_activity(chat_id)
    if new_len == 1 and not await qm.is_active(chat_id):
        await qm.set_starter(chat_id, user_id)
        await play_song(chat_id, song, message)
    else:
        paused = await qm.is_paused(chat_id)
        text = f"✅ **تمت إضافتها للقائمة:** `{song['title']}`\n**الترتيب:** {new_len}"
        thumb = song.get("thumbnail")
        if thumb:
            try:
                await safe_call(
                    message.reply_photo,
                    photo=thumb,
                    caption=text,
                    reply_markup=_controls_kb(paused=paused),
                )
            except Exception:
                await safe_call(message.reply, text, reply_markup=_controls_kb(paused=paused))
        else:
            await safe_call(message.reply, text, reply_markup=_controls_kb(paused=paused))


async def _handle_play_request(message: Message, query: str):
    user_id = message.from_user.id if message.from_user else 0

    # رابط قائمة تشغيل؟
    if is_url(query) and ("list=" in query or "/sets/" in query or "playlist" in query):
        status = await safe_call(message.reply, "🔍 **جاري قراءة قائمة التشغيل...**")
        try:
            items = await fetch_playlist(query, MAX_PLAYLIST_ITEMS)
        except Exception as e:
            logger.exception(e)
            await safe_call(status.edit, GENERIC_ERROR)
            return
        if not items:
            await safe_call(status.edit, GENERIC_ERROR)
            return
        # نحمّل ونشغّل أول عنصر، والبقية تُخزَّن بروابطها وتُحمَّل لاحقاً عند دورها
        await safe_call(
            status.edit,
            f"📃 **تمت إضافة {len(items)} عنصر من القائمة (الحد {MAX_PLAYLIST_ITEMS}).**",
        )
        first = items[0]
        try:
            song = await fetch_audio(first["url"])
        except Exception as e:
            logger.exception(e)
            await safe_call(message.reply, GENERIC_ERROR)
            return
        song["requester_id"] = user_id
        await _start_or_queue(message, song, user_id)
        # بقية العناصر كمراجع غير محمّلة (path يُملأ عند التشغيل)
        chat_id = message.chat.id
        rest = [
            {"title": it["title"], "duration": it["duration"],
             "url": it["url"], "requester_id": user_id, "path": None}
            for it in items[1:]
        ]
        if rest:
            await qm.push_many(chat_id, rest)
        return

    if is_url(query):
        status = await safe_call(message.reply, "🔍 **جاري التحميل...**")
        try:
            song = await fetch_audio(query)
        except Exception as e:
            logger.exception(e)
            await safe_call(status.edit, GENERIC_ERROR)
            return
        song["requester_id"] = user_id
        await safe_call(status.delete)
        await _start_or_queue(message, song, user_id)
    else:
        await _do_search(message, query)


# --- أوامر عامة -------------------------------------------------------------
@bot.on_message(filters.command("start") | filters.regex(RX_START))
async def start_cmd(_, message: Message):
    await safe_call(message.reply_text, HELP_TEXT)


@bot.on_message(filters.command("ping") | filters.regex(RX_PING))
async def ping_cmd(_, message: Message):
    await safe_call(message.reply, "🏓 **بونق!** البوت يعمل بشكل ممتاز.")


@bot.on_message(filters.command("play") & filters.group)
async def play_cmd(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    # فحص وضع الأدمن فقط — مالك البوت معفى دائماً
    if await qm.is_admin_only(chat_id) and not (
        (OWNER_ID and user_id == OWNER_ID) or await is_chat_admin(chat_id, user_id)
    ):
        await safe_call(message.reply, "🚫 **وضع الأدمن فقط مفعّل — التشغيل مقيّد بالمشرفين.**")
        return
    # رد على ملف صوتي/فيديو؟ → شغّله مباشرة بدون بحث
    if _has_playable_media(message.reply_to_message):
        await _handle_telegram_file_play(message)
        return
    if len(message.command) < 2 and not (
        message.reply_to_message and message.reply_to_message.text
    ):
        await safe_call(
            message.reply,
            "❌ **اكتب اسم أغنية أو ضع رابطاً!**\n"
            "مثال: `تشغيل ديسباسيتو` أو `/play despacito`",
        )
        return
    if message.reply_to_message and message.reply_to_message.text:
        query = message.reply_to_message.text
    else:
        query = message.text.split(maxsplit=1)[1]
    await _handle_play_request(message, query)


@bot.on_message(filters.regex(RX_PLAY) & filters.group)
async def play_arabic_cmd(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    # فحص وضع الأدمن فقط — مالك البوت معفى دائماً
    if await qm.is_admin_only(chat_id) and not (
        (OWNER_ID and user_id == OWNER_ID) or await is_chat_admin(chat_id, user_id)
    ):
        await safe_call(message.reply, "🚫 **وضع الأدمن فقط مفعّل — التشغيل مقيّد بالمشرفين.**")
        return
    m = re.match(RX_PLAY, message.text or "")
    if not m:
        return
    query = m.group(1).strip()
    if not query:
        await safe_call(message.reply, "❌ **اكتب اسم أغنية بعد كلمة 'تشغيل'.**")
        return
    await _handle_play_request(message, query)


@bot.on_message(filters.regex(RX_PLAY_BARE) & filters.reply & filters.group)
async def play_arabic_bare_cmd(_, message: Message):
    """معالج 'تشغيل' وحيدة (بدون نص) كرد على ملف صوتي/فيديو."""
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    # فحص وضع الأدمن فقط — مالك البوت معفى دائماً
    if await qm.is_admin_only(chat_id) and not (
        (OWNER_ID and user_id == OWNER_ID) or await is_chat_admin(chat_id, user_id)
    ):
        await safe_call(message.reply, "🚫 **وضع الأدمن فقط مفعّل — التشغيل مقيّد بالمشرفين.**")
        return
    if _has_playable_media(message.reply_to_message):
        await _handle_telegram_file_play(message)
    else:
        await safe_call(
            message.reply,
            "❌ **اكتب اسم أغنية بعد كلمة 'تشغيل'، أو ردّ على ملف صوتي/فيديو.**",
        )


# --- أوامر التحكم (محمية) ----------------------------------------------------
@bot.on_message((filters.command("pause") | filters.regex(RX_PAUSE)) & filters.group)
async def pause_cmd(_, message: Message):
    chat_id, user_id = message.chat.id, (message.from_user.id if message.from_user else 0)
    if not await can_control(chat_id, user_id):
        await safe_call(message.reply, NO_PERMISSION)
        return
    if await qm.is_active(chat_id):
        call_py = await assistants.call_for(chat_id)
        target = await qm.target_chat_id(chat_id)
        await safe_call(call_py.pause_stream, target)
        await qm.set_paused(chat_id, True)
        await safe_call(
            message.reply,
            "⏸ **تم إيقاف الموسيقى مؤقتاً.**",
            reply_markup=_controls_kb(paused=True),
        )
    else:
        await safe_call(message.reply, "❌ **لا توجد موسيقى قيد التشغيل!**")


@bot.on_message((filters.command("resume") | filters.regex(RX_RESUME)) & filters.group)
async def resume_cmd(_, message: Message):
    chat_id, user_id = message.chat.id, (message.from_user.id if message.from_user else 0)
    if not await can_control(chat_id, user_id):
        await safe_call(message.reply, NO_PERMISSION)
        return
    if await qm.is_active(chat_id):
        call_py = await assistants.call_for(chat_id)
        target = await qm.target_chat_id(chat_id)
        await safe_call(call_py.resume_stream, target)
        await qm.set_paused(chat_id, False)
        await safe_call(
            message.reply,
            "▶ **تمت متابعة التشغيل.**",
            reply_markup=_controls_kb(paused=False),
        )
    else:
        await safe_call(message.reply, "❌ **لا توجد موسيقى قيد التشغيل!**")


@bot.on_message((filters.command("skip") | filters.regex(RX_SKIP)) & filters.group)
async def skip_cmd(_, message: Message):
    chat_id, user_id = message.chat.id, (message.from_user.id if message.from_user else 0)
    if not await can_control(chat_id, user_id):
        await safe_call(message.reply, NO_PERMISSION)
        return
    await _skip(chat_id, message)


async def _skip(chat_id: int, message: Message):
    if await qm.queue_length(chat_id) == 0:
        await safe_call(message.reply, "❌ **القائمة فارغة!**")
        return
    await qm.pop_song(chat_id)
    await qm.touch_activity(chat_id)
    if await qm.queue_length(chat_id) > 0:
        await _play_head_resolving(chat_id, message)
        await safe_call(message.reply, "⏭ **تم الانتقال للأغنية التالية.**")
    else:
        await _leave(chat_id)
        await qm.set_active(chat_id, False)
        await safe_call(message.reply, "⏭ **انتهت القائمة.**")


@bot.on_message((filters.command("stop") | filters.regex(RX_STOP)) & filters.group)
async def stop_cmd(_, message: Message):
    chat_id, user_id = message.chat.id, (message.from_user.id if message.from_user else 0)
    if not await can_control(chat_id, user_id):
        await safe_call(message.reply, NO_PERMISSION)
        return
    await _stop(chat_id, message)


async def _stop(chat_id: int, message: Message):
    await qm.clear_queue(chat_id)
    await qm.set_active(chat_id, False)
    await _leave(chat_id)
    await safe_call(message.reply, "⏹ **تم إيقاف الموسيقى ومسح القائمة.**")


@bot.on_message((filters.command("queue") | filters.regex(RX_QUEUE)) & filters.group)
async def queue_cmd(_, message: Message):
    chat_id = message.chat.id
    songs = await qm.get_queue(chat_id)
    if not songs:
        await safe_call(message.reply, "📭 **القائمة فارغة!**")
        return
    loop_on = await qm.is_loop(chat_id)
    text = "🎶 **قائمة الانتظار:**" + (" 🔁" if loop_on else "") + "\n\n"
    for i, song in enumerate(songs, 1):
        text += f"{i}. `{song['title'][:60]}` [{_fmt_duration(song.get('duration'))}]\n"
    text += f"\n**المجموع:** {len(songs)} أغنية"
    await safe_call(message.reply, text)


@bot.on_message((filters.command("loop") | filters.regex(RX_LOOP)) & filters.group)
async def loop_cmd(_, message: Message):
    chat_id, user_id = message.chat.id, (message.from_user.id if message.from_user else 0)
    if not await can_control(chat_id, user_id):
        await safe_call(message.reply, NO_PERMISSION)
        return
    new_state = await _toggle_loop(chat_id)
    await safe_call(
        message.reply,
        "🔁 **تم تفعيل التكرار للأغنية الحالية.**" if new_state else "➡️ **تم إلغاء التكرار.**",
    )


async def _toggle_loop(chat_id: int) -> bool:
    """تبديل وضع التكرار — ترجع الحالة الجديدة."""
    new_state = not await qm.is_loop(chat_id)
    await qm.set_loop(chat_id, new_state)
    return new_state


@bot.on_message((filters.command("shuffle") | filters.regex(RX_SHUFFLE)) & filters.group)
async def shuffle_cmd(_, message: Message):
    chat_id, user_id = message.chat.id, (message.from_user.id if message.from_user else 0)
    if not await can_control(chat_id, user_id):
        await safe_call(message.reply, NO_PERMISSION)
        return
    n = await _do_shuffle(chat_id)
    await safe_call(
        message.reply,
        "🔀 **لا يوجد ما يكفي من الأغاني للخلط.**" if n == 0 else f"🔀 **تم خلط {n} أغنية.**",
    )


async def _do_shuffle(chat_id: int) -> int:
    """خلط القائمة — ترجع عدد الأغاني التي تم خلطها."""
    return await qm.shuffle_queue(chat_id)


@bot.on_message((filters.command("video") | filters.regex(RX_VIDEO)) & filters.group)
async def video_cmd(_, message: Message):
    chat_id, user_id = message.chat.id, (message.from_user.id if message.from_user else 0)
    if not await can_control(chat_id, user_id):
        await safe_call(message.reply, NO_PERMISSION)
        return
    await _toggle_video(chat_id, message)


async def _toggle_video(chat_id: int, message: Message):
    new_state = not await qm.is_video(chat_id)
    await qm.set_video(chat_id, new_state)
    # إعادة تشغيل الأغنية الحالية بالنمط الجديد إن كانت هناك واحدة
    current = await qm.peek_song(chat_id)
    if current and current.get("path") and await qm.is_active(chat_id):
        try:
            call_py = await assistants.call_for(chat_id)
            target = await qm.target_chat_id(chat_id)
            await safe_call(call_py.play, target, _make_stream(current["path"], new_state))
        except Exception:
            logger.exception("video toggle replay error")
    await safe_call(
        message.reply,
        "🎬 **تم التبديل إلى وضع الفيديو.**" if new_state else "🔊 **تم التبديل إلى الصوت فقط.**",
    )


# --- وضع الأدمن فقط للتشغيل -------------------------------------------------
@bot.on_message(
    (filters.command("adminplay") | filters.regex(RX_ADMINPLAY)) & filters.group
)
async def adminplay_cmd(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    # الصلاحية: مالك البوت أو أدمن تيليجرام فعلي فقط (starter وauth users مستثنيان عمداً)
    if not ((OWNER_ID and user_id == OWNER_ID) or await is_chat_admin(chat_id, user_id)):
        await safe_call(message.reply, "🚫 **هذا الأمر للمشرفين الفعليين فقط.**")
        return

    # استخراج المعطى من الأمر السلاشي أو النص العربي
    if message.command:
        arg = message.command[1].strip().lower() if len(message.command) > 1 else ""
    else:
        m = re.match(RX_ADMINPLAY, message.text or "")
        arg = (m.group(1) or "").strip().lower() if m else ""

    if arg in ("تشغيل", "on"):
        await qm.set_admin_only(chat_id, True)
        await safe_call(
            message.reply,
            "🔒 **تم تفعيل وضع الأدمن فقط.**\n"
            "التشغيل الآن مقيّد بالمشرفين — الأعضاء العاديون لن يتمكنوا من بدء تشغيل أغاني.",
        )
    elif arg in ("ايقاف", "إيقاف", "off"):
        await qm.set_admin_only(chat_id, False)
        await safe_call(
            message.reply,
            "🔓 **تم تعطيل وضع الأدمن فقط.**\n"
            "التشغيل متاح الآن لجميع الأعضاء.",
        )
    else:
        # عرض الحالة الحالية
        enabled = await qm.is_admin_only(chat_id)
        state = "🔒 **مفعّل**" if enabled else "🔓 **معطّل**"
        await safe_call(
            message.reply,
            f"ℹ️ **وضع الأدمن فقط:** {state}\n\n"
            "للتفعيل: `وضع_ادمن تشغيل` أو `/adminplay on`\n"
            "للتعطيل: `وضع_ادمن ايقاف` أو `/adminplay off`",
        )


# --- إحصائيات (المالك فقط) ---------------------------------------------------
@bot.on_message(filters.command("stats") | filters.regex(RX_STATS))
async def stats_cmd(_, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if not OWNER_ID or user_id != OWNER_ID:
        return
    data = await stats.summary()
    text = (
        "📊 **إحصائيات البوت:**\n\n"
        f"• المجموعات النشطة: **{data['active_chats']}**\n"
        f"• تشغيلات اليوم: **{data['plays_today']}**\n"
        f"• الحسابات المساعدة: **{assistants.count()}**\n\n"
        "**أكثر الأغاني طلباً:**\n"
    )
    if data["top_songs"]:
        for i, (title, score) in enumerate(data["top_songs"], 1):
            text += f"{i}. `{title[:50]}` — {score}\n"
    else:
        text += "_لا توجد بيانات بعد._"
    await safe_call(message.reply, text)


# --- حل العنصر التالي (قد يكون مرجعاً غير محمّل من قائمة تشغيل) --------------
async def _play_head_resolving(chat_id: int, message: Message):
    song = await qm.peek_song(chat_id)
    if song is None:
        await qm.set_active(chat_id, False)
        await _leave(chat_id)
        return
    if not song.get("path"):
        # عنصر قائمة تشغيل لم يُحمَّل بعد → حمّله الآن
        try:
            resolved = await fetch_audio(song["url"])
        except Exception as e:
            logger.exception(e)
            await qm.pop_song(chat_id)  # تخطّى العنصر المعطوب
            await _play_head_resolving(chat_id, message)
            return
        song.update(resolved)
        # استبدال العنصر الأول بالنسخة المحملة
        await qm.pop_song(chat_id)
        await qm.requeue_front(chat_id, song)
    await play_song(chat_id, song, message)


# --- أزرار البحث ------------------------------------------------------------
@bot.on_callback_query(filters.regex(r"^sel:(\d+)$"))
async def on_select_result(_, cq: CallbackQuery):
    idx = int(cq.matches[0].group(1))
    chat_id, user_id = cq.message.chat.id, cq.from_user.id
    key = f"search:{chat_id}:{cq.message.id}"
    raw = await redis_client.get(key)
    if not raw:
        await safe_call(cq.answer, "انتهت صلاحية نتائج البحث، أعد البحث.", show_alert=True)
        return
    results = json.loads(raw)
    if idx < 0 or idx >= len(results):
        await safe_call(cq.answer, "اختيار غير صالح.", show_alert=True)
        return
    chosen = results[idx]
    await safe_call(cq.answer, "جاري التحميل...")
    await redis_client.delete(key)
    await safe_call(cq.message.edit, f"⬇️ **جاري تحميل:** `{chosen['title']}`")
    try:
        song = await fetch_audio(chosen["url"])
    except Exception as e:
        logger.exception(e)
        await safe_call(cq.message.edit, GENERIC_ERROR)
        return
    song["requester_id"] = user_id
    await _start_or_queue(cq.message, song, user_id)


# --- أزرار التحكم -----------------------------------------------------------
@bot.on_callback_query(filters.regex(r"^ctl:(pause|resume|skip|stop|loop|shuffle)$"))
async def on_control_button(_, cq: CallbackQuery):
    action = cq.matches[0].group(1)
    chat_id, user_id = cq.message.chat.id, cq.from_user.id
    if not await can_control(chat_id, user_id):
        await safe_call(cq.answer, "هذا الزر للمشرفين أو لمن بدأ التشغيل.", show_alert=True)
        return

    if action == "pause":
        if await qm.is_active(chat_id):
            call_py = await assistants.call_for(chat_id)
            target = await qm.target_chat_id(chat_id)
            await safe_call(call_py.pause_stream, target)
            await qm.set_paused(chat_id, True)
            await safe_call(cq.answer, "⏸ تم الإيقاف المؤقت.")
            await safe_call(cq.message.edit_reply_markup, reply_markup=_controls_kb(paused=True))
        else:
            await safe_call(cq.answer, "لا توجد موسيقى قيد التشغيل.", show_alert=True)

    elif action == "resume":
        if await qm.is_active(chat_id):
            call_py = await assistants.call_for(chat_id)
            target = await qm.target_chat_id(chat_id)
            await safe_call(call_py.resume_stream, target)
            await qm.set_paused(chat_id, False)
            await safe_call(cq.answer, "▶️ تمت متابعة التشغيل.")
            await safe_call(cq.message.edit_reply_markup, reply_markup=_controls_kb(paused=False))
        else:
            await safe_call(cq.answer, "لا توجد موسيقى قيد التشغيل.", show_alert=True)

    elif action == "skip":
        await safe_call(cq.answer, "⏭ تخطّي...")
        await _skip(chat_id, cq.message)

    elif action == "stop":
        await safe_call(cq.answer, "⏹ إيقاف.")
        await _stop(chat_id, cq.message)

    elif action == "loop":
        new_state = await _toggle_loop(chat_id)
        label = "🔁 تم تفعيل التكرار." if new_state else "➡️ تم إلغاء التكرار."
        await safe_call(cq.answer, label)
        paused = await qm.is_paused(chat_id)
        await safe_call(cq.message.edit_reply_markup, reply_markup=_controls_kb(paused=paused))

    elif action == "shuffle":
        n = await _do_shuffle(chat_id)
        label = "🔀 لا يوجد ما يكفي للخلط." if n == 0 else f"🔀 تم خلط {n} أغنية."
        await safe_call(cq.answer, label)
        paused = await qm.is_paused(chat_id)
        await safe_call(cq.message.edit_reply_markup, reply_markup=_controls_kb(paused=paused))


@bot.on_callback_query(filters.regex(r"^vid:toggle$"))
async def on_video_toggle(_, cq: CallbackQuery):
    chat_id, user_id = cq.message.chat.id, cq.from_user.id
    if not await can_control(chat_id, user_id):
        await safe_call(cq.answer, "هذا الزر للمشرفين أو لمن بدأ التشغيل.", show_alert=True)
        return
    await safe_call(cq.answer, "🎬 تبديل النمط...")
    await _toggle_video(chat_id, cq.message)


# --- أمر ربط القناة (Channel Play) -------------------------------------------
@bot.on_message(
    (filters.command("cplay") | filters.regex(RX_CPLAY)) & filters.group
)
async def cplay_cmd(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    # الصلاحية: مالك البوت أو أدمن تيليجرام فعلي فقط (starter وauth users مستثنيان عمداً — تغيير بنيوي)
    if not ((OWNER_ID and user_id == OWNER_ID) or await is_chat_admin(chat_id, user_id)):
        await safe_call(message.reply, "🚫 **هذا الأمر للمشرفين الفعليين فقط.**")
        return

    # استخراج المعطى
    if message.command:
        arg = message.command[1].strip().lower() if len(message.command) > 1 else ""
    else:
        m = re.match(RX_CPLAY, message.text or "")
        arg = (m.group(1) or "").strip().lower() if m else ""

    if arg in ("linked", "مرتبطة", "الرابطة", "linked_channel"):
        # ربط بالقناة المرتبطة
        try:
            chat = await bot.get_chat(chat_id)
            linked = getattr(chat, "linked_chat", None)
        except Exception as e:
            logger.exception(e)
            await safe_call(message.reply, "❌ **تعذّر جلب معلومات المجموعة، حاول مرة أخرى.**")
            return

        if linked is None:
            await safe_call(
                message.reply,
                "❌ **لا توجد قناة مرتبطة بهذه المجموعة.**\n"
                "تأكد من ربط قناة بالمجموعة من إعدادات تيليجرام أولاً.",
            )
            return

        await qm.set_cplay_target(chat_id, linked.id)
        await safe_call(
            message.reply,
            f"✅ **تم ربط المجموعة بالقناة:** `{linked.title}`\n\n"
            "🔊 **سيُبث الصوت الآن عبر Voice Chat القناة.**\n\n"
            "⚠️ **تنبيه مهم:**\n"
            "• يجب أن يكون **Voice Chat نشطاً فعلياً** في **القناة** (وليس المجموعة)\n"
            "• يجب أن يكون **الحساب المساعد عضواً** في القناة وله صلاحية الانضمام للمكالمة الصوتية\n"
            "• تفاعل المستخدمين (الأوامر، الردود) يبقى في المجموعة كما هو",
        )

    elif arg in ("الغاء", "إلغاء", "off", "cancel", "ايقاف"):
        # إلغاء الربط
        old = await qm.get_cplay_target(chat_id)
        if old is None:
            await safe_call(message.reply, "ℹ️ **لا يوجد ربط قناة مفعّل حالياً.**")
            return
        await qm.set_cplay_target(chat_id, None)
        await safe_call(
            message.reply,
            "✅ **تم إلغاء ربط القناة.**\n"
            "الصوت سيُبث الآن عبر Voice Chat المجموعة مباشرة.",
        )

    else:
        # عرض الحالة الحالية (بدون معطى أو 'حالة')
        target = await qm.get_cplay_target(chat_id)
        if target is None:
            status_text = "❌ **لا يوجد ربط قناة حالياً.**"
        else:
            try:
                ch = await bot.get_chat(target)
                ch_name = ch.title
            except Exception:
                ch_name = str(target)
            status_text = f"✅ **مربوطة بالقناة:** `{ch_name}` (`{target}`)"

        await safe_call(
            message.reply,
            f"📡 **حالة ربط القناة:**\n{status_text}\n\n"
            "**الأوامر المتاحة:**\n"
            "• `ربط_قناة linked` — ربط بالقناة المرتبطة\n"
            "• `ربط_قناة الغاء` — إلغاء الربط\n\n"
            "⚠️ يتطلب Voice Chat نشط في القناة وعضوية الحساب المساعد فيها.",
        )
