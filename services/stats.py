import datetime

from core.clients import redis_client as _redis
from services import queue_manager as qm


def _plays_key(day: str) -> str:
    return f"stats:plays:{day}"


_TOP_KEY = "stats:top_songs"


def _today() -> str:
    return datetime.date.today().isoformat()


async def record_play(title: str) -> None:
    """تسجيل تشغيل: عدّاد يومي + ترتيب أكثر الأغاني طلباً."""
    await _redis.incr(_plays_key(_today()))
    await _redis.zincrby(_TOP_KEY, 1, title[:80])


async def summary(top_n: int = 5) -> dict:
    plays_today = await _redis.get(_plays_key(_today()))
    active = await qm.active_chat_ids()
    top_raw = await _redis.zrevrange(_TOP_KEY, 0, top_n - 1, withscores=True)
    top = [(title, int(score)) for title, score in top_raw]
    return {
        "active_chats": len(active),
        "plays_today": int(plays_today) if plays_today else 0,
        "top_songs": top,
    }
