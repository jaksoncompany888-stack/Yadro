"""
Social Media Providers

Abstract provider architecture for multi-platform posting.
Inspired by Postiz open-source project (26k+ stars).

Architecture:
    SocialProvider (base)
        ├── TelegramProvider - Bot API
        ├── VKProvider - VK API + OAuth2
        └── InstagramProvider - TODO

Usage:
    from app.providers import TelegramProvider, VKProvider

    # Telegram
    tg = TelegramProvider(bot_token="...")
    await tg.post("@channel", "Hello!")

    # VK
    vk = VKProvider(app_id="...", app_secret="...")
    auth_url, state = vk.get_auth_url(redirect_uri)
    # ... user authorizes ...
    token = await vk.exchange_code(code, redirect_uri)
    await vk.post("-123456", "Привет ВК!")
"""

from .base import (
    SocialProvider,
    PostResult,
    MediaItem,
    MediaType,
    ProviderError,
    AuthenticationError,
    RateLimitError,
    PostingError,
)
from .telegram import TelegramProvider, TelegramChannel
from .vk import VKProvider, VKToken, VKGroup
from .manager import ProviderManager, Platform, UserChannel, CrossPostResult

__all__ = [
    # Base
    "SocialProvider",
    "PostResult",
    "MediaItem",
    "MediaType",
    "ProviderError",
    "AuthenticationError",
    "RateLimitError",
    "PostingError",
    # Telegram
    "TelegramProvider",
    "TelegramChannel",
    # VK
    "VKProvider",
    "VKToken",
    "VKGroup",
    # Manager
    "ProviderManager",
    "Platform",
    "UserChannel",
    "CrossPostResult",
]
