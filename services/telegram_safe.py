import asyncio
import logging

from pyrogram.errors import FloodWait

logger = logging.getLogger("music-bot")


async def safe_call(coro_func, *args, max_retries=3, **kwargs):
    """تغليف أي نداء async حساس ضد FloodWait مع إعادة المحاولة."""
    for attempt in range(max_retries):
        try:
            return await coro_func(*args, **kwargs)
        except FloodWait as e:
            wait = getattr(e, "value", None) or getattr(e, "x", 1)
            logger.warning(
                f"FloodWait {wait}s on {getattr(coro_func, '__name__', coro_func)} "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(wait)
    return await coro_func(*args, **kwargs)
