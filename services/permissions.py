import time

from pyrogram.enums import ChatMemberStatus

from config import OWNER_ID, ADMIN_CACHE_TTL
from core.clients import bot
from services import queue_manager as qm

_admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}
_ADMIN_STATUSES = {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR}


async def _is_admin(chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    now = time.time()
    cached = _admin_cache.get(key)
    if cached and cached[1] > now:
        return cached[0]
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        result = member.status in _ADMIN_STATUSES
    except Exception:
        result = False
    _admin_cache[key] = (result, now + ADMIN_CACHE_TTL)
    return result


async def is_chat_admin(chat_id: int, user_id: int) -> bool:
    """فحص إن كان المستخدم أدمن تيليجرام فعلي (owner أو administrator) — دالة عامة."""
    return await _is_admin(chat_id, user_id)


async def can_control(chat_id: int, user_id: int) -> bool:
    if OWNER_ID and user_id == OWNER_ID:
        return True
    if await qm.get_starter(chat_id) == user_id:
        return True
    return await _is_admin(chat_id, user_id)
