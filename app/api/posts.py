"""
Posts API

CRUD operations for posts/drafts.
"""

import json
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.storage.database import Database
from app.smm.agent import SMMAgent
from app.providers import ProviderManager, TelegramProvider, Platform

from .deps import get_db, get_agent, get_current_user
from .models import (
    PostCreate,
    PostUpdate,
    PostResponse,
    PostList,
    PostStatus,
    Platform as APIPlatform,
    MediaAttachment,
    GenerateRequest,
    GenerateResponse,
    EditRequest,
    EditResponse,
    SuccessResponse,
)


router = APIRouter(prefix="/posts", tags=["posts"])


# =============================================================================
# Helpers
# =============================================================================

def _row_to_post(row) -> PostResponse:
    """Convert DB row to PostResponse."""
    # Convert sqlite3.Row to dict for .get() support
    row = dict(row)
    metadata = json.loads(row.get("metadata") or "{}")

    # Parse platforms and channel_ids from metadata
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


# =============================================================================
# CRUD
# =============================================================================

@router.post("", response_model=PostResponse)
async def create_post(
    data: PostCreate,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Create a new post or draft."""

    # Determine status
    status = PostStatus.SCHEDULED if data.publish_at else PostStatus.DRAFT

    # Build metadata
    metadata = {
        "platforms": [p.value for p in data.platforms],
        "channel_ids": data.channel_ids,
        "media": [m.model_dump() for m in data.media],
        **data.metadata,
    }

    # Insert
    db.execute(
        """
        INSERT INTO drafts (user_id, text, topic, channel_id, publish_at, status, metadata, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            user["id"],
            data.text,
            data.topic,
            data.channel_ids.get("telegram", ""),  # Legacy field
            data.publish_at.isoformat() if data.publish_at else None,
            status.value,
            json.dumps(metadata),
        )
    )

    # Get created post
    row = db.fetch_one(
        "SELECT * FROM drafts WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user["id"],)
    )

    return _row_to_post(row)


@router.get("", response_model=PostList)
async def list_posts(
    status: Optional[PostStatus] = None,
    platform: Optional[APIPlatform] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """List user's posts with filters."""

    # Build query
    conditions = ["user_id = ?"]
    params: List = [user["id"]]

    if status:
        conditions.append("status = ?")
        params.append(status.value)

    if from_date:
        conditions.append("(publish_at >= ? OR (publish_at IS NULL AND created_at >= ?))")
        params.extend([from_date.isoformat(), from_date.isoformat()])

    if to_date:
        conditions.append("(publish_at <= ? OR (publish_at IS NULL AND created_at <= ?))")
        params.extend([to_date.isoformat(), to_date.isoformat()])

    where = " AND ".join(conditions)

    # Count total
    total = db.fetch_value(f"SELECT COUNT(*) FROM drafts WHERE {where}", params)

    # Fetch page
    offset = (page - 1) * per_page
    rows = db.fetch_all(
        f"""
        SELECT * FROM drafts
        WHERE {where}
        ORDER BY COALESCE(publish_at, created_at) DESC
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset]
    )

    # Filter by platform in Python (metadata is JSON)
    items = []
    for row in rows:
        post = _row_to_post(row)
        if platform and platform not in post.platforms:
            continue
        items.append(post)

    return PostList(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        has_more=(page * per_page) < total,
    )


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(
    post_id: int,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Get single post by ID."""

    row = db.fetch_one(
        "SELECT * FROM drafts WHERE id = ? AND user_id = ?",
        (post_id, user["id"])
    )

    if not row:
        raise HTTPException(status_code=404, detail="Post not found")

    return _row_to_post(row)


@router.patch("/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: int,
    data: PostUpdate,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Update a post."""

    # Get existing
    row = db.fetch_one(
        "SELECT * FROM drafts WHERE id = ? AND user_id = ?",
        (post_id, user["id"])
    )

    if not row:
        raise HTTPException(status_code=404, detail="Post not found")

    # Can't edit published posts
    if row["status"] == PostStatus.PUBLISHED.value:
        raise HTTPException(status_code=400, detail="Cannot edit published post")

    # Build updates
    updates = []
    params = []

    if data.text is not None:
        updates.append("text = ?")
        params.append(data.text)

    if data.topic is not None:
        updates.append("topic = ?")
        params.append(data.topic)

    if data.publish_at is not None:
        updates.append("publish_at = ?")
        params.append(data.publish_at.isoformat())
        # If adding publish_at, change status to scheduled
        if row["status"] == PostStatus.DRAFT.value:
            updates.append("status = ?")
            params.append(PostStatus.SCHEDULED.value)

    if data.status is not None:
        updates.append("status = ?")
        params.append(data.status.value)

    # Update metadata
    metadata = json.loads(row.get("metadata") or "{}")

    if data.platforms is not None:
        metadata["platforms"] = [p.value for p in data.platforms]

    if data.channel_ids is not None:
        metadata["channel_ids"] = data.channel_ids

    if data.media is not None:
        metadata["media"] = [m.model_dump() for m in data.media]

    if data.metadata is not None:
        metadata.update(data.metadata)

    updates.append("metadata = ?")
    params.append(json.dumps(metadata))

    updates.append("updated_at = datetime('now')")

    # Execute update
    if updates:
        params.append(post_id)
        db.execute(
            f"UPDATE drafts SET {', '.join(updates)} WHERE id = ?",
            params
        )

    # Return updated
    row = db.fetch_one("SELECT * FROM drafts WHERE id = ?", (post_id,))
    return _row_to_post(row)


@router.delete("/{post_id}", response_model=SuccessResponse)
async def delete_post(
    post_id: int,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Delete a post."""

    row = db.fetch_one(
        "SELECT * FROM drafts WHERE id = ? AND user_id = ?",
        (post_id, user["id"])
    )

    if not row:
        raise HTTPException(status_code=404, detail="Post not found")

    db.execute("DELETE FROM drafts WHERE id = ?", (post_id,))

    return SuccessResponse(message="Post deleted")


# =============================================================================
# Publish
# =============================================================================

@router.post("/{post_id}/publish", response_model=PostResponse)
async def publish_post(
    post_id: int,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Publish a post immediately."""
    import os

    row = db.fetch_one(
        "SELECT * FROM drafts WHERE id = ? AND user_id = ?",
        (post_id, user["id"])
    )

    if not row:
        raise HTTPException(status_code=404, detail="Post not found")

    if row["status"] == PostStatus.PUBLISHED.value:
        raise HTTPException(status_code=400, detail="Already published")

    post = _row_to_post(row)
    metadata = json.loads(row.get("metadata") or "{}")

    # Initialize provider
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise HTTPException(status_code=500, detail="Bot not configured")

    published_ids = {}
    published_urls = {}
    errors = []

    # Publish to each platform
    for platform in post.platforms:
        channel_id = post.channel_ids.get(platform.value)
        if not channel_id:
            continue

        if platform == APIPlatform.TELEGRAM:
            provider = TelegramProvider(bot_token=bot_token)
            try:
                result = await provider.post(channel_id, post.text)
                if result.success:
                    published_ids[platform.value] = result.post_id
                    if result.url:
                        published_urls[platform.value] = result.url
                else:
                    errors.append(f"{platform.value}: {result.error}")
            except Exception as e:
                errors.append(f"{platform.value}: {str(e)}")
            finally:
                await provider.close()

        # VK would be similar

    # Update status
    if published_ids:
        metadata["published_ids"] = published_ids
        metadata["published_urls"] = published_urls

        if errors:
            metadata["error_message"] = "; ".join(errors)
            new_status = PostStatus.ERROR.value
        else:
            new_status = PostStatus.PUBLISHED.value

        db.execute(
            """
            UPDATE drafts
            SET status = ?, metadata = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (new_status, json.dumps(metadata), post_id)
        )
    else:
        metadata["error_message"] = "; ".join(errors) if errors else "No channels configured"
        db.execute(
            """
            UPDATE drafts
            SET status = ?, metadata = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (PostStatus.ERROR.value, json.dumps(metadata), post_id)
        )

    # Return updated
    row = db.fetch_one("SELECT * FROM drafts WHERE id = ?", (post_id,))
    return _row_to_post(row)


# =============================================================================
# AI Generation
# =============================================================================

@router.post("/generate", response_model=GenerateResponse)
async def generate_post(
    data: GenerateRequest,
    user: dict = Depends(get_current_user),
    agent: SMMAgent = Depends(get_agent),
):
    """Generate post using AI agent."""

    try:
        if data.with_research:
            draft = agent.generate_post_with_research(user["id"], data.topic)
        else:
            draft = agent.generate_post(user["id"], data.topic, style=data.style)

        return GenerateResponse(
            text=draft.text,
            topic=data.topic,
            suggestions=[],  # Could add alternatives later
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")


@router.post("/edit", response_model=EditResponse)
async def edit_post_ai(
    data: EditRequest,
    user: dict = Depends(get_current_user),
    agent: SMMAgent = Depends(get_agent),
):
    """Edit post using AI agent."""

    try:
        result = agent.edit_post(
            user_id=user["id"],
            original=data.text,
            edit_request=data.instruction,
        )

        return EditResponse(
            text=result,
            changes_made=[data.instruction],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Edit failed: {str(e)}")
