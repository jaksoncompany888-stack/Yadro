"""
FastAPI Application

Main entry point for the API.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .posts import router as posts_router
from .calendar import router as calendar_router
from .channels import router as channels_router
from .deps import get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup: ensure DB has metadata column
    db = get_db()
    _migrate_drafts_table(db)
    yield
    # Shutdown: nothing to do


def _migrate_drafts_table(db):
    """Add metadata column if not exists."""
    try:
        # Check if column exists
        db.execute("SELECT metadata FROM drafts LIMIT 1")
    except Exception:
        # Add column
        db.execute("ALTER TABLE drafts ADD COLUMN metadata TEXT DEFAULT '{}'")
        print("Migration: added metadata column to drafts")


def create_app() -> FastAPI:
    """Create FastAPI application."""

    app = FastAPI(
        title="Yadro SMM API",
        description="API for SMM Agent - posts, calendar, AI generation",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS for Mini App and web
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://web.telegram.org",
            "https://*.telegram.org",
            "http://localhost:3000",
            "http://localhost:5173",
            "*",  # TODO: restrict in production
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(posts_router, prefix="/api")
    app.include_router(calendar_router, prefix="/api")
    app.include_router(channels_router, prefix="/api")

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # API info
    @app.get("/api")
    async def api_info():
        return {
            "name": "Yadro SMM API",
            "version": "1.0.0",
            "endpoints": {
                "posts": "/api/posts",
                "calendar": "/api/calendar",
                "channels": "/api/channels",
                "generate": "/api/posts/generate",
                "edit": "/api/posts/edit",
                "analyze": "/api/channels/analyze",
            }
        }

    # Error handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
            }
        )

    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("API_PORT", 8000))
    uvicorn.run(
        "app.api.app:app",
        host="0.0.0.0",
        port=port,
        reload=True,
    )
