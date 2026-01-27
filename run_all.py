"""
Unified runner for AWS deployment.
Runs both API and Telegram bot in one process.
"""

import asyncio
import threading
import signal
import sys
import os
import uvicorn
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Флаг для graceful shutdown
shutdown_event = threading.Event()


def signal_handler(signum, frame):
    """Обработка сигналов завершения"""
    print(f"[Runner] Received signal {signum}, shutting down...")
    shutdown_event.set()
    sys.exit(0)


def run_api():
    """Run FastAPI in a thread."""
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "app.api.app:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )


async def run_bot():
    """Run Telegram bot."""
    from app.smm.bot import main
    await main()


def main():
    # Регистрируем обработчики сигналов
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Start API in background thread
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    print("[Runner] API started on background thread")

    # Run bot in main thread (asyncio)
    print("[Runner] Starting Telegram bot...")
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        print("[Runner] Shutdown complete")


if __name__ == "__main__":
    main()
