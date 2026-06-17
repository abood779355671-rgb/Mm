import logging

from core.clients import calls, redis_client as _redis

logger = logging.getLogger("music-bot")


def _assign_key(chat_id: int) -> str:
    return f"assistant:{chat_id}"


def _load_key(index: int) -> str:
    return f"assistant_load:{index}"


def count() -> int:
    return len(calls)


async def _least_busy_index() -> int:
    """فهرس الحساب المساعد الأقل ازدحاماً (أقل عدد مكالمات نشطة)."""
    best_index, best_load = 0, None
    for i in range(len(calls)):
        raw = await _redis.get(_load_key(i))
        load = int(raw) if raw else 0
        if best_load is None or load < best_load:
            best_index, best_load = i, load
    return best_index


async def assign(chat_id: int) -> int:
    """ربط مجموعة بحساب مساعد (ثابت). يختار الأقل ازدحاماً عند أول مرة."""
    raw = await _redis.get(_assign_key(chat_id))
    if raw is not None:
        return int(raw)
    index = await _least_busy_index()
    await _redis.set(_assign_key(chat_id), str(index))
    return index


async def get_index(chat_id: int) -> int | None:
    raw = await _redis.get(_assign_key(chat_id))
    return int(raw) if raw is not None else None


def get_call(index: int):
    return calls[index]


async def call_for(chat_id: int):
    """إرجاع كائن PyTgCalls المسؤول عن هذه المجموعة (يربطها إن لزم)."""
    index = await get_index(chat_id)
    if index is None:
        index = await assign(chat_id)
    return calls[index]


async def incr_load(chat_id: int) -> None:
    index = await get_index(chat_id)
    if index is not None:
        await _redis.incr(_load_key(index))


async def decr_load(chat_id: int) -> None:
    index = await get_index(chat_id)
    if index is not None:
        # لا تهبط تحت الصفر
        cur = await _redis.get(_load_key(index))
        if cur and int(cur) > 0:
            await _redis.decr(_load_key(index))
    await _redis.delete(_assign_key(chat_id))
