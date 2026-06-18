import json
import time

from core.clients import redis_client as _redis


def _queue_key(chat_id: int) -> str:
    return f"queue:{chat_id}"


def _active_key(chat_id: int) -> str:
    return f"active:{chat_id}"


def _loop_key(chat_id: int) -> str:
    return f"loop:{chat_id}"


def _starter_key(chat_id: int) -> str:
    return f"starter:{chat_id}"


def _activity_key(chat_id: int) -> str:
    return f"activity:{chat_id}"


def _video_key(chat_id: int) -> str:
    return f"video:{chat_id}"


def _paused_key(chat_id: int) -> str:
    return f"paused:{chat_id}"


def _adminplay_key(chat_id: int) -> str:
    return f"adminplay:{chat_id}"


def _cplay_key(chat_id: int) -> str:
    return f"cplay:{chat_id}"


def _cplay_reverse_key(channel_id: int) -> str:
    return f"cplay_reverse:{channel_id}"


# --- القائمة ----------------------------------------------------------------
async def get_queue(chat_id: int) -> list[dict]:
    raw = await _redis.lrange(_queue_key(chat_id), 0, -1)
    return [json.loads(item) for item in raw]


async def push_song(chat_id: int, song: dict) -> int:
    return await _redis.rpush(_queue_key(chat_id), json.dumps(song))


async def push_many(chat_id: int, songs: list[dict]) -> int:
    if not songs:
        return await queue_length(chat_id)
    return await _redis.rpush(_queue_key(chat_id), *[json.dumps(s) for s in songs])


async def pop_song(chat_id: int) -> dict | None:
    raw = await _redis.lpop(_queue_key(chat_id))
    return json.loads(raw) if raw else None


async def peek_song(chat_id: int) -> dict | None:
    raw = await _redis.lindex(_queue_key(chat_id), 0)
    return json.loads(raw) if raw else None


async def queue_length(chat_id: int) -> int:
    return await _redis.llen(_queue_key(chat_id))


async def clear_queue(chat_id: int) -> None:
    await _redis.delete(
        _queue_key(chat_id),
        _loop_key(chat_id),
        _starter_key(chat_id),
        _activity_key(chat_id),
        _video_key(chat_id),
        _paused_key(chat_id),
    )


async def requeue_front(chat_id: int, song: dict) -> None:
    await _redis.lpush(_queue_key(chat_id), json.dumps(song))


async def shuffle_queue(chat_id: int) -> int:
    import random

    songs = await get_queue(chat_id)
    if len(songs) <= 2:
        return 0
    head, rest = songs[0], songs[1:]
    random.shuffle(rest)
    new_list = [head] + rest
    async with _redis.pipeline(transaction=True) as pipe:
        pipe.delete(_queue_key(chat_id))
        pipe.rpush(_queue_key(chat_id), *[json.dumps(s) for s in new_list])
        await pipe.execute()
    return len(rest)


# --- الحالة النشطة ----------------------------------------------------------
async def is_active(chat_id: int) -> bool:
    return bool(await _redis.exists(_active_key(chat_id)))


async def set_active(chat_id: int, value: bool) -> None:
    if value:
        await _redis.set(_active_key(chat_id), "1")
    else:
        await _redis.delete(_active_key(chat_id))


# --- وضع التكرار ------------------------------------------------------------
async def is_loop(chat_id: int) -> bool:
    return bool(await _redis.exists(_loop_key(chat_id)))


async def set_loop(chat_id: int, value: bool) -> None:
    if value:
        await _redis.set(_loop_key(chat_id), "1")
    else:
        await _redis.delete(_loop_key(chat_id))


# --- نمط الفيديو ------------------------------------------------------------
async def is_video(chat_id: int) -> bool:
    return bool(await _redis.exists(_video_key(chat_id)))


async def set_video(chat_id: int, value: bool) -> None:
    if value:
        await _redis.set(_video_key(chat_id), "1")
    else:
        await _redis.delete(_video_key(chat_id))


# --- وضع الإيقاف المؤقت ----------------------------------------------------
async def is_paused(chat_id: int) -> bool:
    return bool(await _redis.exists(_paused_key(chat_id)))


async def set_paused(chat_id: int, value: bool) -> None:
    if value:
        await _redis.set(_paused_key(chat_id), "1")
    else:
        await _redis.delete(_paused_key(chat_id))


# --- وضع الأدمن فقط للتشغيل -------------------------------------------------
async def is_admin_only(chat_id: int) -> bool:
    return bool(await _redis.exists(_adminplay_key(chat_id)))


async def set_admin_only(chat_id: int, value: bool) -> None:
    if value:
        await _redis.set(_adminplay_key(chat_id), "1")
    else:
        await _redis.delete(_adminplay_key(chat_id))


# --- مالك الجلسة ------------------------------------------------------------
async def set_starter(chat_id: int, user_id: int) -> None:
    await _redis.set(_starter_key(chat_id), str(user_id))


async def get_starter(chat_id: int) -> int | None:
    raw = await _redis.get(_starter_key(chat_id))
    return int(raw) if raw else None


# --- تتبّع النشاط -----------------------------------------------------------
async def touch_activity(chat_id: int) -> None:
    await _redis.set(_activity_key(chat_id), str(time.time()))


async def get_last_activity(chat_id: int) -> float | None:
    raw = await _redis.get(_activity_key(chat_id))
    return float(raw) if raw else None


async def active_chat_ids() -> list[int]:
    keys = await _redis.keys("active:*")
    return [int(k.split(":", 1)[1]) for k in keys]


# --- ربط القناة (Channel Play) ----------------------------------------------
async def set_cplay_target(chat_id: int, channel_id: int | None) -> None:
    """ربط مجموعة بقناة أو إلغاء الربط (channel_id=None)."""
    if channel_id is None:
        old = await get_cplay_target(chat_id)
        if old is not None:
            await _redis.delete(_cplay_reverse_key(old))
        await _redis.delete(_cplay_key(chat_id))
    else:
        await _redis.set(_cplay_key(chat_id), str(channel_id))
        await _redis.set(_cplay_reverse_key(channel_id), str(chat_id))


async def get_cplay_target(chat_id: int) -> int | None:
    """يرجع معرّف القناة المربوطة بالمجموعة، أو None إن لم يوجد ربط."""
    raw = await _redis.get(_cplay_key(chat_id))
    return int(raw) if raw else None


async def target_chat_id(chat_id: int) -> int:
    """المعرف الذي يجب استخدامه فعلياً مع PyTgCalls: القناة المربوطة إن وُجدت، وإلا chat_id نفسه."""
    target = await get_cplay_target(chat_id)
    return target if target is not None else chat_id


async def resolve_group_chat_id(maybe_channel_id: int) -> int:
    """عكس target_chat_id: إن كان المعرف الوارد قناة مربوطة، يرجع معرف المجموعة الأصلية. وإلا يرجع نفس المعرف."""
    raw = await _redis.get(_cplay_reverse_key(maybe_channel_id))
    return int(raw) if raw else maybe_channel_id
