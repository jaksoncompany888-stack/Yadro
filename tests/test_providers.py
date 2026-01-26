"""
Tests for Social Media Providers
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.providers import (
    SocialProvider,
    PostResult,
    MediaItem,
    MediaType,
    TelegramProvider,
    VKProvider,
    ProviderManager,
    Platform,
    UserChannel,
    CrossPostResult,
)


# =============================================================================
# Base Classes
# =============================================================================

class TestPostResult:
    """Tests for PostResult dataclass."""

    def test_ok_result(self):
        result = PostResult.ok(
            post_id="123",
            url="https://t.me/channel/123",
            platform="telegram"
        )
        assert result.success is True
        assert result.post_id == "123"
        assert result.url == "https://t.me/channel/123"
        assert result.platform == "telegram"
        assert result.error is None

    def test_fail_result(self):
        result = PostResult.fail("Network error", platform="vk")
        assert result.success is False
        assert result.error == "Network error"
        assert result.platform == "vk"
        assert result.post_id is None

    def test_raw_response(self):
        result = PostResult.ok(
            post_id="456",
            platform="telegram",
            raw={"message_id": 456, "chat_id": -123}
        )
        assert result.raw_response == {"message_id": 456, "chat_id": -123}


class TestMediaItem:
    """Tests for MediaItem dataclass."""

    def test_media_with_url(self):
        item = MediaItem(type=MediaType.IMAGE, url="https://example.com/image.jpg")
        assert item.type == MediaType.IMAGE
        assert item.url == "https://example.com/image.jpg"

    def test_media_with_file_path(self):
        item = MediaItem(type=MediaType.VIDEO, file_path="/path/to/video.mp4")
        assert item.type == MediaType.VIDEO
        assert item.file_path == "/path/to/video.mp4"

    def test_media_with_file_id(self):
        item = MediaItem(type=MediaType.DOCUMENT, file_id="AgACAgIAAxk...")
        assert item.type == MediaType.DOCUMENT
        assert item.file_id == "AgACAgIAAxk..."

    def test_media_requires_source(self):
        with pytest.raises(ValueError) as exc:
            MediaItem(type=MediaType.IMAGE)
        assert "requires url, file_path, or file_id" in str(exc.value)

    def test_media_with_caption(self):
        item = MediaItem(
            type=MediaType.IMAGE,
            url="https://example.com/image.jpg",
            caption="My photo"
        )
        assert item.caption == "My photo"


class TestCrossPostResult:
    """Tests for CrossPostResult."""

    def test_all_success(self):
        result = CrossPostResult(
            results={
                "telegram:@ch1": PostResult.ok("1", platform="telegram"),
                "vk:-123": PostResult.ok("2", platform="vk"),
            },
            successful=["telegram:@ch1", "vk:-123"],
            failed=[],
        )
        assert result.all_success is True
        assert result.partial_success is False
        assert result.all_failed is False

    def test_partial_success(self):
        result = CrossPostResult(
            results={
                "telegram:@ch1": PostResult.ok("1", platform="telegram"),
                "vk:-123": PostResult.fail("Error", platform="vk"),
            },
            successful=["telegram:@ch1"],
            failed=["vk:-123"],
        )
        assert result.all_success is False
        assert result.partial_success is True
        assert result.all_failed is False

    def test_all_failed(self):
        result = CrossPostResult(
            results={
                "telegram:@ch1": PostResult.fail("Error 1", platform="telegram"),
                "vk:-123": PostResult.fail("Error 2", platform="vk"),
            },
            successful=[],
            failed=["telegram:@ch1", "vk:-123"],
        )
        assert result.all_success is False
        assert result.partial_success is False
        assert result.all_failed is True

    def test_summary(self):
        result = CrossPostResult(
            results={"telegram:@ch1": PostResult.ok("1", platform="telegram")},
            successful=["telegram:@ch1"],
            failed=[],
        )
        assert "Posted to 1" in result.summary()


# =============================================================================
# Telegram Provider
# =============================================================================

class TestTelegramProvider:
    """Tests for TelegramProvider."""

    def test_init(self):
        provider = TelegramProvider(bot_token="123:ABC")
        assert provider.name == "telegram"
        assert provider.max_text_length == 4096
        assert provider.max_media_per_post == 10
        assert provider.supports_media is True

    def test_normalize_channel_id(self):
        provider = TelegramProvider(bot_token="123:ABC")

        # Already has @
        assert provider._normalize_channel_id("@mychannel") == "@mychannel"

        # Without @
        assert provider._normalize_channel_id("mychannel") == "@mychannel"

        # Numeric ID (negative)
        assert provider._normalize_channel_id("-1001234567890") == "-1001234567890"

    def test_truncate_text(self):
        provider = TelegramProvider(bot_token="123:ABC")

        # Short text - no change
        assert provider.truncate_text("Hello") == "Hello"

        # Long text - truncated
        long_text = "x" * 5000
        truncated = provider.truncate_text(long_text)
        assert len(truncated) == 4096
        assert truncated.endswith("...")

    def test_format_text(self):
        provider = TelegramProvider(bot_token="123:ABC")

        # Bold
        assert provider.format_text("**bold**") == "<b>bold</b>"

        # Italic
        assert provider.format_text("*italic*") == "<i>italic</i>"

        # Code
        assert provider.format_text("`code`") == "<code>code</code>"

        # Link
        assert provider.format_text("[text](https://example.com)") == '<a href="https://example.com">text</a>'

    def test_split_media(self):
        provider = TelegramProvider(bot_token="123:ABC")

        # Less than limit
        media = [MediaItem(type=MediaType.IMAGE, url=f"https://example.com/{i}.jpg") for i in range(5)]
        chunks = provider.split_media(media)
        assert len(chunks) == 1
        assert len(chunks[0]) == 5

        # More than limit
        media = [MediaItem(type=MediaType.IMAGE, url=f"https://example.com/{i}.jpg") for i in range(15)]
        chunks = provider.split_media(media)
        assert len(chunks) == 2
        assert len(chunks[0]) == 10
        assert len(chunks[1]) == 5

    def test_build_message_url(self):
        provider = TelegramProvider(bot_token="123:ABC")

        # With username
        url = provider._build_message_url("@mychannel", 123)
        assert url == "https://t.me/mychannel/123"

        # Numeric ID (no URL possible)
        url = provider._build_message_url("-1001234567890", 123)
        assert url is None

    def test_extract_retry_after(self):
        provider = TelegramProvider(bot_token="123:ABC")

        # With retry_after
        result = provider._extract_retry_after("Flood control exceeded. Retry after 30 seconds.")
        assert result == 30

        # Without retry_after
        result = provider._extract_retry_after("Unknown error")
        assert result is None


# =============================================================================
# VK Provider
# =============================================================================

class TestVKProvider:
    """Tests for VKProvider."""

    def test_init(self):
        provider = VKProvider(app_id="123", app_secret="secret")
        assert provider.name == "vk"
        assert provider.max_text_length == 15895
        assert provider.max_media_per_post == 10
        assert provider.supports_formatting is False

    def test_normalize_group_id(self):
        provider = VKProvider(app_id="123", app_secret="secret")

        # Just number
        assert provider._normalize_group_id("123456") == -123456

        # Already negative
        assert provider._normalize_group_id("-123456") == -123456

        # With 'club' prefix
        assert provider._normalize_group_id("club123456") == -123456

        # With 'public' prefix
        assert provider._normalize_group_id("public123456") == -123456

    def test_get_auth_url(self):
        provider = VKProvider(app_id="123", app_secret="secret")
        url, state = provider.get_auth_url("https://example.com/callback")

        assert "oauth.vk.com/authorize" in url
        assert "client_id=123" in url
        assert "redirect_uri=https://example.com/callback" in url
        assert "code_challenge=" in url
        assert state is not None
        assert len(state) > 20

    def test_pkce_challenge(self):
        provider = VKProvider(app_id="123", app_secret="secret")
        verifier = "test_verifier_12345"
        challenge = provider._generate_pkce_challenge(verifier)

        # Should be base64url encoded
        assert challenge is not None
        assert len(challenge) > 20
        assert "+" not in challenge  # base64url doesn't use +
        assert "/" not in challenge  # base64url doesn't use /


# =============================================================================
# Provider Manager
# =============================================================================

class TestProviderManager:
    """Tests for ProviderManager."""

    def test_init(self):
        manager = ProviderManager()
        assert manager.available_platforms == []

    def test_register_provider(self):
        manager = ProviderManager()
        provider = TelegramProvider(bot_token="123:ABC")
        manager.register_provider("telegram", provider)

        assert "telegram" in manager.available_platforms
        assert manager.get_provider("telegram") is provider

    def test_add_channel(self):
        manager = ProviderManager()
        channel = UserChannel(
            platform=Platform.TELEGRAM,
            channel_id="@mychannel",
            channel_name="My Channel",
        )
        manager.add_channel(user_id=1, channel=channel)

        channels = manager.get_user_channels(user_id=1)
        assert len(channels) == 1
        assert channels[0].channel_id == "@mychannel"

    def test_add_channel_duplicate_updates(self):
        manager = ProviderManager()

        channel1 = UserChannel(
            platform=Platform.TELEGRAM,
            channel_id="@mychannel",
            channel_name="Old Name",
        )
        manager.add_channel(user_id=1, channel=channel1)

        channel2 = UserChannel(
            platform=Platform.TELEGRAM,
            channel_id="@mychannel",
            channel_name="New Name",
        )
        manager.add_channel(user_id=1, channel=channel2)

        channels = manager.get_user_channels(user_id=1)
        assert len(channels) == 1
        assert channels[0].channel_name == "New Name"

    def test_remove_channel(self):
        manager = ProviderManager()
        channel = UserChannel(
            platform=Platform.TELEGRAM,
            channel_id="@mychannel",
            channel_name="My Channel",
        )
        manager.add_channel(user_id=1, channel=channel)
        manager.remove_channel(user_id=1, platform=Platform.TELEGRAM, channel_id="@mychannel")

        channels = manager.get_user_channels(user_id=1)
        assert len(channels) == 0

    def test_get_user_channels_filter_by_platform(self):
        manager = ProviderManager()

        manager.add_channel(user_id=1, channel=UserChannel(
            platform=Platform.TELEGRAM,
            channel_id="@tg",
            channel_name="TG",
        ))
        manager.add_channel(user_id=1, channel=UserChannel(
            platform=Platform.VK,
            channel_id="-123",
            channel_name="VK",
        ))

        # All channels
        all_channels = manager.get_user_channels(user_id=1)
        assert len(all_channels) == 2

        # Only Telegram
        tg_channels = manager.get_user_channels(user_id=1, platform=Platform.TELEGRAM)
        assert len(tg_channels) == 1
        assert tg_channels[0].channel_id == "@tg"

    def test_strip_html(self):
        manager = ProviderManager()

        # Bold
        assert manager._strip_html("<b>bold</b>") == "bold"

        # Italic
        assert manager._strip_html("<i>italic</i>") == "italic"

        # Link - keeps text
        assert manager._strip_html('<a href="https://example.com">link</a>') == "link"

        # Mixed
        text = "<b>Bold</b> and <i>italic</i> with <a href=\"url\">link</a>"
        assert manager._strip_html(text) == "Bold and italic with link"


# =============================================================================
# Platform Enum
# =============================================================================

class TestPlatform:
    """Tests for Platform enum."""

    def test_values(self):
        assert Platform.TELEGRAM.value == "telegram"
        assert Platform.VK.value == "vk"
        assert Platform.INSTAGRAM.value == "instagram"

    def test_string_comparison(self):
        assert Platform.TELEGRAM == "telegram"
        assert Platform.VK == "vk"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
