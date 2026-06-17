import asyncio
import logging
import os

from aiohttp import web

from core.clients import LOOP, bot, userbots, calls
from services.downloader import cleanup_downloads_loop
from handlers.idle_watcher import idle_watcher_loop
from handlers.call_events import register_call_handlers

# استيراد الهاندلرز يسجّل أوامر bot وأزرار callback
import handlers.commands  # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("music-bot")


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


async def main():
    await bot.start()
    # تشغيل كل الحسابات المساعدة
    for call_py in calls:
        await call_py.start()
    register_call_handlers()
    await run_web_server()
    asyncio.create_task(cleanup_downloads_loop())
    asyncio.create_task(idle_watcher_loop())
    me = await bot.get_me()
    logger.info(f"Bot started as @{me.username} with {len(calls)} assistant(s)")
    print(f"Bot started as @{me.username} with {len(calls)} assistant(s)", flush=True)

    from pytgcalls import idle

    await idle()
    await bot.stop()


if __name__ == "__main__":
    try:
        LOOP.run_until_complete(main())
    finally:
        LOOP.close()
