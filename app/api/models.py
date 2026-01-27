"""
API Models (Pydantic)

Request/Response schemas for the API.
Inspired by Postiz's structured approach with Zod.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================

class PostStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    ERROR = "error"


class Platform(str, Enum):
    TELEGRAM = "telegram"
    VK = "vk"


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"


# =============================================================================
# Media
# =============================================================================

class MediaAttachment(BaseModel):
    """Media attachment for a post."""
    type: MediaType
    url: Optional[str] = None
    file_id: Optional[str] = None  # Platform-specific file ID
    caption: Optional[str] = None


# =============================================================================
# Posts
# =============================================================================

class PostCreate(BaseModel):
    """Create a new post/draft."""
    text: str = Field(..., min_length=1, max_length=15000)
    topic: Optional[str] = None
    platforms: List[Platform] = Field(default=[Platform.TELEGRAM])
    channel_ids: Dict[Platform, str] = Field(default_factory=dict)  # platform -> channel_id
    media: List[MediaAttachment] = Field(default_factory=list)
    publish_at: Optional[datetime] = None  # None = draft, datetime = scheduled
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": "Новый пост для канала!",
                "topic": "продуктивность",
                "platforms": ["telegram", "vk"],
                "channel_ids": {
                    "telegram": "@mychannel",
                    "vk": "-123456"
                },
                "publish_at": "2025-01-27T10:00:00Z"
            }
        }
    }


class PostUpdate(BaseModel):
    """Update existing post."""
    text: Optional[str] = Field(None, min_length=1, max_length=15000)
    topic: Optional[str] = None
    platforms: Optional[List[Platform]] = None
    channel_ids: Optional[Dict[Platform, str]] = None
    media: Optional[List[MediaAttachment]] = None
    publish_at: Optional[datetime] = None
    status: Optional[PostStatus] = None
    metadata: Optional[Dict[str, Any]] = None


class PostResponse(BaseModel):
    """Post response."""
    id: int
    user_id: int
    text: str
    topic: Optional[str]
    platforms: List[Platform]
    channel_ids: Dict[Platform, str]
    media: List[MediaAttachment]
    publish_at: Optional[datetime]
    status: PostStatus
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    # Published info
    published_ids: Dict[Platform, str] = Field(default_factory=dict)  # platform -> post_id
    published_urls: Dict[Platform, str] = Field(default_factory=dict)  # platform -> url
    error_message: Optional[str] = None

    model_config = {"from_attributes": True}


class PostList(BaseModel):
    """Paginated list of posts."""
    items: List[PostResponse]
    total: int
    page: int
    per_page: int
    has_more: bool


# =============================================================================
# Calendar
# =============================================================================

class CalendarDay(BaseModel):
    """Single day in calendar."""
    date: str  # YYYY-MM-DD
    posts: List[PostResponse]
    count: int


class CalendarResponse(BaseModel):
    """Calendar view response."""
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    days: List[CalendarDay]
    total_posts: int
    total_scheduled: int
    total_published: int


# =============================================================================
# AI Generation
# =============================================================================

class GenerateRequest(BaseModel):
    """Request AI to generate post."""
    topic: str = Field(..., min_length=1, max_length=500)
    style: Optional[str] = None
    platform: Platform = Platform.TELEGRAM
    with_research: bool = False


class GenerateResponse(BaseModel):
    """AI-generated post."""
    text: str
    topic: str
    suggestions: List[str] = Field(default_factory=list)  # Alternative versions


class EditRequest(BaseModel):
    """Request AI to edit post."""
    text: str
    instruction: str = Field(..., min_length=1, max_length=500)


class EditResponse(BaseModel):
    """Edited post."""
    text: str
    changes_made: List[str] = Field(default_factory=list)


# =============================================================================
# Channels
# =============================================================================

class ChannelInfo(BaseModel):
    """Connected channel info."""
    platform: Platform
    channel_id: str
    name: str
    is_active: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChannelList(BaseModel):
    """List of user's channels."""
    channels: List[ChannelInfo]


# =============================================================================
# Auth
# =============================================================================

class TokenResponse(BaseModel):
    """Auth token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class UserInfo(BaseModel):
    """Current user info."""
    id: int
    tg_id: int
    username: Optional[str]
    channels: List[ChannelInfo] = Field(default_factory=list)


# =============================================================================
# Common
# =============================================================================

class SuccessResponse(BaseModel):
    """Generic success response."""
    success: bool = True
    message: str = "OK"


class ErrorResponse(BaseModel):
    """Error response."""
    success: bool = False
    error: str
    details: Optional[Dict[str, Any]] = None
