"""
Yadro API Layer

REST API for calendar, posts, and integrations.
Used by web dashboard and Telegram Mini App.

Run:
    python -m app.api.app
    # or
    uvicorn app.api.app:app --reload

Endpoints:
    GET  /health              - Health check
    GET  /api                 - API info

    POST /api/posts           - Create post
    GET  /api/posts           - List posts
    GET  /api/posts/{id}      - Get post
    PATCH /api/posts/{id}     - Update post
    DELETE /api/posts/{id}    - Delete post
    POST /api/posts/{id}/publish - Publish now

    POST /api/posts/generate  - AI generate post
    POST /api/posts/edit      - AI edit post

    GET  /api/calendar        - Calendar view
    GET  /api/calendar/week   - Week view
    GET  /api/calendar/month  - Month view
    GET  /api/calendar/today  - Today's posts
    GET  /api/calendar/slots  - Available time slots

Auth:
    Telegram Mini App sends init_data in header:
    X-Telegram-Init-Data: query_id=...&user={...}&hash=...
"""

from .app import create_app, app
from .posts import router as posts_router
from .calendar import router as calendar_router

__all__ = [
    "create_app",
    "app",
    "posts_router",
    "calendar_router",
]
