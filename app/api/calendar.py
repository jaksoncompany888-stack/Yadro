"""
Calendar API

Calendar view endpoints for scheduling.
"""

import json
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

from fastapi import APIRouter, Depends, Query

from app.storage.database import Database

from .deps import get_db, get_current_user
from .models import (
    CalendarResponse,
    CalendarDay,
    PostResponse,
    PostStatus,
    Platform as APIPlatform,
    MediaAttachment,
)


router = APIRouter(prefix="/calendar", tags=["calendar"])


def _row_to_post(row) -> PostResponse:
    """Convert DB row to PostResponse."""
    # Convert sqlite3.Row to dict for .get() support
    row = dict(row)
    metadata = json.loads(row.get("metadata") or "{}")

    platforms = metadata.get("platforms", ["telegram"])
    channel_ids = metadata.get("channel_ids", {})
    media = metadata.get("media", [])
    published_ids = metadata.get("published_ids", {})
    published_urls = metadata.get("published_urls", {})

    return PostResponse(
        id=row["id"],
        user_id=row["user_id"],
        text=row["text"],
        topic=row.get("topic"),
        platforms=[APIPlatform(p) for p in platforms],
        channel_ids=channel_ids,
        media=[MediaAttachment(**m) for m in media],
        publish_at=datetime.fromisoformat(row["publish_at"]) if row.get("publish_at") else None,
        status=PostStatus(row["status"]),
        metadata=metadata,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        published_ids=published_ids,
        published_urls=published_urls,
        error_message=metadata.get("error_message"),
    )


@router.get("", response_model=CalendarResponse)
async def get_calendar(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    days: int = Query(7, ge=1, le=90, description="Number of days if no end_date"),
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """
    Get calendar view with posts.

    Returns posts grouped by day for the specified date range.
    Default: 7 days starting from today.
    """

    # Parse dates
    if start_date:
        start = datetime.strptime(start_date, "%Y-%m-%d")
    else:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if end_date:
        end = datetime.strptime(end_date, "%Y-%m-%d")
    else:
        end = start + timedelta(days=days)

    # Fetch posts in range
    rows = db.fetch_all(
        """
        SELECT * FROM drafts
        WHERE user_id = ?
          AND (
            (publish_at IS NOT NULL AND publish_at >= ? AND publish_at < ?)
            OR
            (publish_at IS NULL AND status = 'draft' AND created_at >= ? AND created_at < ?)
          )
        ORDER BY COALESCE(publish_at, created_at) ASC
        """,
        (
            user["id"],
            start.isoformat(),
            (end + timedelta(days=1)).isoformat(),
            start.isoformat(),
            (end + timedelta(days=1)).isoformat(),
        )
    )

    # Group by day
    posts_by_day = defaultdict(list)
    for row in rows:
        post = _row_to_post(row)
        if post.publish_at:
            day = post.publish_at.strftime("%Y-%m-%d")
        else:
            day = post.created_at.strftime("%Y-%m-%d")
        posts_by_day[day].append(post)

    # Build response
    calendar_days = []
    current = start
    total_scheduled = 0
    total_published = 0

    while current <= end:
        day_str = current.strftime("%Y-%m-%d")
        day_posts = posts_by_day.get(day_str, [])

        for p in day_posts:
            if p.status == PostStatus.SCHEDULED:
                total_scheduled += 1
            elif p.status == PostStatus.PUBLISHED:
                total_published += 1

        calendar_days.append(CalendarDay(
            date=day_str,
            posts=day_posts,
            count=len(day_posts),
        ))

        current += timedelta(days=1)

    return CalendarResponse(
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        days=calendar_days,
        total_posts=len(rows),
        total_scheduled=total_scheduled,
        total_published=total_published,
    )


@router.get("/week", response_model=CalendarResponse)
async def get_week(
    offset: int = Query(0, description="Week offset (0=current, 1=next, -1=previous)"),
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Get calendar for a week."""

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    # Start from Monday
    start = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    end = start + timedelta(days=6)

    return await get_calendar(
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        user=user,
        db=db,
    )


@router.get("/month", response_model=CalendarResponse)
async def get_month(
    year: Optional[int] = None,
    month: Optional[int] = Query(None, ge=1, le=12),
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Get calendar for a month."""

    today = datetime.now()
    year = year or today.year
    month = month or today.month

    start = datetime(year, month, 1)

    # End of month
    if month == 12:
        end = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = datetime(year, month + 1, 1) - timedelta(days=1)

    return await get_calendar(
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        user=user,
        db=db,
    )


@router.get("/today")
async def get_today(
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Get posts for today."""

    today = datetime.now().strftime("%Y-%m-%d")

    return await get_calendar(
        start_date=today,
        end_date=today,
        user=user,
        db=db,
    )


@router.get("/slots")
async def get_available_slots(
    date: str = Query(..., description="Date YYYY-MM-DD"),
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """
    Get suggested posting times for a day.

    Returns available time slots based on:
    - Best engagement times
    - Already scheduled posts
    """

    # Parse date
    target_date = datetime.strptime(date, "%Y-%m-%d")

    # Best times for engagement (can be personalized later)
    best_times = [
        "09:00", "12:00", "15:00", "18:00", "21:00"
    ]

    # Get already scheduled posts
    rows = db.fetch_all(
        """
        SELECT publish_at FROM drafts
        WHERE user_id = ?
          AND publish_at >= ?
          AND publish_at < ?
          AND status IN ('scheduled', 'published')
        """,
        (
            user["id"],
            target_date.isoformat(),
            (target_date + timedelta(days=1)).isoformat(),
        )
    )

    # Find taken slots
    taken_times = set()
    for row in rows:
        if row["publish_at"]:
            dt = datetime.fromisoformat(row["publish_at"])
            taken_times.add(dt.strftime("%H:%M"))

    # Build slots
    slots = []
    for time_str in best_times:
        is_available = time_str not in taken_times

        # Check if time already passed for today
        if target_date.date() == datetime.now().date():
            slot_time = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M")
            if slot_time < datetime.now():
                is_available = False

        slots.append({
            "time": time_str,
            "datetime": f"{date}T{time_str}:00",
            "available": is_available,
            "recommended": time_str in ["09:00", "18:00"],  # Peak times
        })

    return {
        "date": date,
        "slots": slots,
        "timezone": "Europe/Moscow",  # TODO: from user settings
    }
