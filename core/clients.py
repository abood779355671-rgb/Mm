import asyncio

import redis.asyncio as aioredis
from pyrogram import Client
from pytgcalls import PyTgCalls

from config import API_ID, API_HASH, BOT_TOKEN, SESSIONS, REDIS_URL

# ضبط الـloop قبل إنشاء أي Client (Kurigram يلتقط get_event_loop عند الإنشاء).
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

bot = Client(
    name="music_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

# إنشاء حساب مساعد + PyTgCalls لكل جلسة
userbots: list[Client] = []
calls: list[PyTgCalls] = []

for _i, _session in enumerate(SESSIONS):
    _ub = Client(
        name=f"assistant_{_i}",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=_session,
        in_memory=True,
    )
    userbots.append(_ub)
    calls.append(PyTgCalls(_ub))

# عميل Redis مشترك
redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
