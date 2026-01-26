"""
Provider Manager

Coordinates multi-platform posting and manages provider instances.
"""

import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .base import SocialProvider, PostResult, MediaItem


class Platform(str, Enum):
    """Supported social platforms."""
    TELEGRAM = "telegram"
    VK = "vk"
    INSTAGRAM = "instagram"  # TODO


@dataclass
class CrossPostResult:
    """Result of posting to multiple platforms."""
    results: Dict[str, PostResult] = field(default_factory=dict)
    successful: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)

    @property
    def all_success(self) -> bool:
        return len(self.failed) == 0 and len(self.successful) > 0

    @property
    def partial_success(self) -> bool:
        return len(self.successful) > 0 and len(self.failed) > 0

    @property
    def all_failed(self) -> bool:
        return len(self.successful) == 0 and len(self.failed) > 0

    def summary(self) -> str:
        """Human-readable summary."""
        if self.all_success:
            return f"Posted to {len(self.successful)} platform(s)"
        elif self.partial_success:
            return f"Posted to {len(self.successful)}, failed on {len(self.failed)}"
        elif self.all_failed:
            errors = [self.results[p].error for p in self.failed if self.results[p].error]
            return f"Failed on all {len(self.failed)} platform(s): {'; '.join(errors[:2])}"
        else:
            return "No platforms configured"


@dataclass
class UserChannel:
    """User's connected channel/group."""
    platform: Platform
    channel_id: str
    channel_name: str
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class ProviderManager:
    """
    Manages social media providers for a user.

    Handles:
    - Provider registration and configuration
    - Cross-platform posting
    - Token storage and refresh
    - Rate limiting coordination

    Usage:
        manager = ProviderManager()

        # Register providers
        manager.register_provider("telegram", TelegramProvider(bot_token="..."))
        manager.register_provider("vk", VKProvider(app_id="...", app_secret="..."))

        # Add user channels
        manager.add_channel(user_id, UserChannel(
            platform=Platform.TELEGRAM,
            channel_id="@mychannel",
            channel_name="My Channel",
        ))

        # Cross-post
        result = await manager.cross_post(
            user_id,
            text="Hello everyone!",
            platforms=[Platform.TELEGRAM, Platform.VK],
        )
    """

    def __init__(self):
        self._providers: Dict[str, SocialProvider] = {}
        self._user_channels: Dict[int, List[UserChannel]] = {}

    def register_provider(self, name: str, provider: SocialProvider):
        """
        Register a provider instance.

        Args:
            name: Provider name (should match Platform enum value)
            provider: Provider instance
        """
        self._providers[name] = provider

    def get_provider(self, name: str) -> Optional[SocialProvider]:
        """Get provider by name."""
        return self._providers.get(name)

    def add_channel(self, user_id: int, channel: UserChannel):
        """
        Add a channel for user.

        Args:
            user_id: User ID
            channel: Channel configuration
        """
        if user_id not in self._user_channels:
            self._user_channels[user_id] = []

        # Check for duplicate
        for existing in self._user_channels[user_id]:
            if existing.platform == channel.platform and existing.channel_id == channel.channel_id:
                # Update existing
                existing.channel_name = channel.channel_name
                existing.enabled = channel.enabled
                existing.metadata = channel.metadata
                return

        self._user_channels[user_id].append(channel)

    def remove_channel(self, user_id: int, platform: Platform, channel_id: str):
        """Remove a channel for user."""
        if user_id not in self._user_channels:
            return

        self._user_channels[user_id] = [
            ch for ch in self._user_channels[user_id]
            if not (ch.platform == platform and ch.channel_id == channel_id)
        ]

    def get_user_channels(self, user_id: int, platform: Optional[Platform] = None) -> List[UserChannel]:
        """
        Get user's connected channels.

        Args:
            user_id: User ID
            platform: Optional filter by platform

        Returns:
            List of user channels
        """
        channels = self._user_channels.get(user_id, [])
        if platform:
            channels = [ch for ch in channels if ch.platform == platform]
        return channels

    async def post(
        self,
        user_id: int,
        platform: Platform,
        channel_id: str,
        text: str,
        media: Optional[List[MediaItem]] = None,
        **kwargs
    ) -> PostResult:
        """
        Post to a single platform.

        Args:
            user_id: User ID (for logging/context)
            platform: Target platform
            channel_id: Channel/group ID
            text: Post text
            media: Optional media attachments
            **kwargs: Platform-specific options

        Returns:
            PostResult
        """
        provider = self._providers.get(platform.value)
        if not provider:
            return PostResult.fail(
                f"Provider not configured: {platform.value}",
                platform=platform.value
            )

        # Adapt text to platform
        adapted_text = self._adapt_text(text, platform)

        return await provider.post(channel_id, adapted_text, media, **kwargs)

    async def cross_post(
        self,
        user_id: int,
        text: str,
        media: Optional[List[MediaItem]] = None,
        platforms: Optional[List[Platform]] = None,
        **kwargs
    ) -> CrossPostResult:
        """
        Post to multiple platforms simultaneously.

        Args:
            user_id: User ID
            text: Post text (will be adapted per platform)
            media: Optional media attachments
            platforms: List of platforms (None = all enabled channels)
            **kwargs: Platform-specific options

        Returns:
            CrossPostResult with results per platform
        """
        result = CrossPostResult()

        # Get target channels
        channels = self.get_user_channels(user_id)
        if platforms:
            channels = [ch for ch in channels if ch.platform in platforms]
        channels = [ch for ch in channels if ch.enabled]

        if not channels:
            return result

        # Post to all platforms in parallel
        tasks = []
        for channel in channels:
            tasks.append(self._post_to_channel(channel, text, media, **kwargs))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        for channel, post_result in zip(channels, results):
            platform_key = f"{channel.platform.value}:{channel.channel_id}"

            if isinstance(post_result, Exception):
                result.results[platform_key] = PostResult.fail(
                    str(post_result),
                    platform=channel.platform.value
                )
                result.failed.append(platform_key)
            elif post_result.success:
                result.results[platform_key] = post_result
                result.successful.append(platform_key)
            else:
                result.results[platform_key] = post_result
                result.failed.append(platform_key)

        return result

    async def _post_to_channel(
        self,
        channel: UserChannel,
        text: str,
        media: Optional[List[MediaItem]],
        **kwargs
    ) -> PostResult:
        """Post to a single channel."""
        provider = self._providers.get(channel.platform.value)
        if not provider:
            return PostResult.fail(
                f"Provider not configured: {channel.platform.value}",
                platform=channel.platform.value
            )

        # Adapt text to platform
        adapted_text = self._adapt_text(text, channel.platform)

        return await provider.post(channel.channel_id, adapted_text, media, **kwargs)

    def _adapt_text(self, text: str, platform: Platform) -> str:
        """
        Adapt text for specific platform.

        Different platforms have different:
        - Character limits
        - Formatting support
        - Hashtag conventions
        """
        provider = self._providers.get(platform.value)
        if not provider:
            return text

        # Truncate if needed
        if len(text) > provider.max_text_length:
            text = text[:provider.max_text_length - 3] + "..."

        # Platform-specific adaptations
        if platform == Platform.VK:
            # VK doesn't support HTML formatting
            text = self._strip_html(text)
        elif platform == Platform.TELEGRAM:
            # Telegram uses HTML, convert markdown if needed
            text = provider.format_text(text)

        return text

    def _strip_html(self, text: str) -> str:
        """Remove HTML tags from text."""
        import re
        # Convert <b> to nothing (VK has no bold)
        text = re.sub(r'<b>|</b>', '', text)
        text = re.sub(r'<i>|</i>', '', text)
        text = re.sub(r'<code>|</code>', '', text)
        text = re.sub(r'<a href="[^"]*">([^<]*)</a>', r'\1', text)
        return text

    async def validate_all_channels(self, user_id: int) -> Dict[str, bool]:
        """
        Validate all user's channels.

        Returns:
            Dict mapping channel key to validity
        """
        channels = self.get_user_channels(user_id)
        results = {}

        for channel in channels:
            provider = self._providers.get(channel.platform.value)
            if not provider:
                results[f"{channel.platform.value}:{channel.channel_id}"] = False
                continue

            try:
                valid = await provider.validate_channel(channel.channel_id)
                results[f"{channel.platform.value}:{channel.channel_id}"] = valid
            except Exception:
                results[f"{channel.platform.value}:{channel.channel_id}"] = False

        return results

    async def health_check(self) -> Dict[str, bool]:
        """
        Check health of all providers.

        Returns:
            Dict mapping provider name to health status
        """
        results = {}

        for name, provider in self._providers.items():
            try:
                healthy = await provider.health_check()
                results[name] = healthy
            except Exception:
                results[name] = False

        return results

    @property
    def available_platforms(self) -> List[str]:
        """List of configured providers."""
        return list(self._providers.keys())
