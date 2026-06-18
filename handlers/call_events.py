import logging

from pytgcalls.types import MediaStream

from core.clients import calls
from services import queue_manager as qm
from services import assistants
from services import stats
from services.downloader import fetch_audio
from services.telegram_safe import safe_call

logger = logging.getLogger("music-bot")


def _make_stream(path: str, video: bool) -> MediaStream:
    if video:
        return MediaStream(path)
    return MediaStream(path, video_flags=MediaStream.Flags.IGNORE)


async def _leave(chat_id: int):
    try:
        call_py = await assistants.call_for(chat_id)
        target = await qm.target_chat_id(chat_id)
        await safe_call(call_py.leave_call, target)
    except Exception:
        pass
    await assistants.decr_load(chat_id)


async def _on_stream_end(chat_id: int):
    # تصحيح المعرّف: إن كان chat_id قناة مربوطة، حوّله لمعرّف المجموعة الأصلية
    chat_id = await qm.resolve_group_chat_id(chat_id)

    # وضع التكرار: أعد الأغنية الحالية لمقدمة القائمة بدل حذفها
    if await qm.is_loop(chat_id):
        current = await qm.peek_song(chat_id)
        await qm.pop_song(chat_id)
        if current is not None:
            await qm.requeue_front(chat_id, current)
    else:
        await qm.pop_song(chat_id)

    await qm.touch_activity(chat_id)
    song = await qm.peek_song(chat_id)
    if song is None:
        await qm.set_active(chat_id, False)
        await _leave(chat_id)
        return

    # عنصر قائمة تشغيل غير محمّل بعد
    if not song.get("path"):
        try:
            resolved = await fetch_audio(song["url"])
        except Exception as e:
            logger.exception(e)
            await qm.pop_song(chat_id)
            await _on_stream_end(chat_id)
            return
        song.update(resolved)
        await qm.pop_song(chat_id)
        await qm.requeue_front(chat_id, song)

    call_py = await assistants.call_for(chat_id)
    video = await qm.is_video(chat_id)
    target = await qm.target_chat_id(chat_id)
    await safe_call(call_py.play, target, _make_stream(song["path"], video))
    await stats.record_play(song.get("title", "Unknown"))


def register_call_handlers():
    """تسجيل معالج StreamEnded على كل حساب مساعد."""
    from pytgcalls.types import StreamEnded

    for call_py in calls:

        @call_py.on_update()
        async def _handler(_, update, __cp=call_py):
            try:
                if isinstance(update, StreamEnded):
                    await _on_stream_end(update.chat_id)
            except Exception:
                logger.exception("on_call_update error")
