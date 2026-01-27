"""
Unified runner for Render deployment.
Runs both API and Telegram bot in one process.
"""

import asyncio
import threading
import os
import uvicorn


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
    # Start API in background thread
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    print("[Runner] API started on background thread")

    # Run bot in main thread (asyncio)
    print("[Runner] Starting Telegram bot...")
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
