import asyncio
import logging
import time

from config import IDLE_TIMEOUT_MINUTES
from services import queue_manager as qm
from services import assistants
from services.telegram_safe import safe_call

logger = logging.getLogger("music-bot")

_CHECK_INTERVAL = 120  # كل دقيقتين


async def idle_watcher_loop():
    timeout_seconds = IDLE_TIMEOUT_MINUTES * 60
    while True:
        await asyncio.sleep(_CHECK_INTERVAL)
        try:
            now = time.time()
            for chat_id in await qm.active_chat_ids():
                if await qm.queue_length(chat_id) > 0:
                    continue
                last = await qm.get_last_activity(chat_id)
                if last is not None and (now - last) < timeout_seconds:
                    continue
                try:
                    call_py = await assistants.call_for(chat_id)
                    await safe_call(call_py.leave_call, chat_id)
                except Exception:
                    pass
                await assistants.decr_load(chat_id)
                await qm.set_active(chat_id, False)
                logger.info(f"idle_watcher: left idle call in chat {chat_id}")
        except Exception:
            logger.exception("idle_watcher_loop error")
