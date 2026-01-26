"""
Base Social Provider

Abstract base class for all social media providers.
Each platform (Telegram, VK, Instagram) implements this interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime


class MediaType(str, Enum):
    """Supported media types."""
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"


@dataclass
class MediaItem:
    """Media attachment for post."""
    type: MediaType
    url: Optional[str] = None          # URL to fetch from
    file_path: Optional[str] = None    # Local file path
    file_id: Optional[str] = None      # Platform-specific file ID (for reuse)
    caption: Optional[str] = None      # Optional caption for this media

    def __post_init__(self):
        if not any([self.url, self.file_path, self.file_id]):
            raise ValueError("MediaItem requires url, file_path, or file_id")


@dataclass
class PostResult:
    """Result of posting to social platform."""
    success: bool
    post_id: Optional[str] = None      # Platform-specific post ID
    url: Optional[str] = None          # URL to the published post
    error: Optional[str] = None        # Error message if failed
    platform: str = ""                 # Provider name
    raw_response: Optional[Dict] = None  # Raw API response for debugging

    @classmethod
    def ok(cls, post_id: str, url: str = None, platform: str = "", raw: Dict = None) -> "PostResult":
        return cls(success=True, post_id=post_id, url=url, platform=platform, raw_response=raw)

    @classmethod
    def fail(cls, error: str, platform: str = "") -> "PostResult":
        return cls(success=False, error=error, platform=platform)


@dataclass
class ScheduledPost:
    """Post scheduled for future publication."""
    text: str
    scheduled_time: datetime
    media: List[MediaItem] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """Base exception for provider errors."""
    pass


class AuthenticationError(ProviderError):
    """Failed to authenticate with the platform."""
    pass


class RateLimitError(ProviderError):
    """Rate limit exceeded."""
    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class PostingError(ProviderError):
    """Failed to publish post."""
    pass


class SocialProvider(ABC):
    """
    Abstract base class for social media providers.

    Each platform implements:
    - Authentication (OAuth, bot token, etc.)
    - Posting (text, media, scheduled)
    - Platform-specific formatting

    Usage:
        provider = TelegramProvider(bot_token="...")
        result = await provider.post(channel_id, "Hello world!")
    """

    # Provider metadata (override in subclass)
    name: str = "base"
    display_name: str = "Base Provider"

    # Platform limits
    max_text_length: int = 4096
    max_media_per_post: int = 10
    supports_media: bool = True
    supports_scheduling: bool = False
    supports_formatting: bool = True  # Bold, italic, etc.

    # Rate limiting
    max_requests_per_second: float = 1.0

    @abstractmethod
    async def post(
        self,
        channel_id: str,
        text: str,
        media: Optional[List[MediaItem]] = None,
        reply_to: Optional[str] = None,
        **kwargs
    ) -> PostResult:
        """
        Publish a post to the platform.

        Args:
            channel_id: Platform-specific channel/page/group identifier
            text: Post text (will be truncated if exceeds max_text_length)
            media: Optional list of media attachments
            reply_to: Optional post ID to reply to
            **kwargs: Platform-specific options

        Returns:
            PostResult with success status and post details
        """
        pass

    @abstractmethod
    async def validate_channel(self, channel_id: str) -> bool:
        """
        Check if channel exists and bot has posting permissions.

        Args:
            channel_id: Platform-specific channel identifier

        Returns:
            True if channel is valid and accessible
        """
        pass

    async def schedule_post(
        self,
        channel_id: str,
        text: str,
        scheduled_time: datetime,
        media: Optional[List[MediaItem]] = None,
        **kwargs
    ) -> PostResult:
        """
        Schedule a post for future publication.

        Default implementation stores locally; platforms with native
        scheduling (like Telegram) can override.
        """
        if not self.supports_scheduling:
            return PostResult.fail(
                f"{self.display_name} does not support scheduling",
                platform=self.name
            )
        raise NotImplementedError("Subclass must implement schedule_post")

    async def delete_post(self, channel_id: str, post_id: str) -> bool:
        """
        Delete a published post.

        Args:
            channel_id: Channel identifier
            post_id: Post identifier to delete

        Returns:
            True if deleted successfully
        """
        raise NotImplementedError("Subclass must implement delete_post")

    async def edit_post(
        self,
        channel_id: str,
        post_id: str,
        new_text: str,
        **kwargs
    ) -> PostResult:
        """
        Edit an existing post.

        Args:
            channel_id: Channel identifier
            post_id: Post identifier to edit
            new_text: New text content

        Returns:
            PostResult with updated post details
        """
        raise NotImplementedError("Subclass must implement edit_post")

    def format_text(self, text: str) -> str:
        """
        Apply platform-specific formatting.

        Converts generic formatting (markdown or HTML) to platform format.
        Default: return as-is.
        """
        return text

    def truncate_text(self, text: str) -> str:
        """Truncate text to platform limit with ellipsis."""
        if len(text) <= self.max_text_length:
            return text
        return text[:self.max_text_length - 3] + "..."

    def split_media(self, media: List[MediaItem]) -> List[List[MediaItem]]:
        """Split media into chunks respecting platform limits."""
        if not media:
            return []
        chunks = []
        for i in range(0, len(media), self.max_media_per_post):
            chunks.append(media[i:i + self.max_media_per_post])
        return chunks

    async def health_check(self) -> bool:
        """
        Check if provider is working correctly.

        Returns:
            True if provider can connect to the platform
        """
        return True

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name}>"
