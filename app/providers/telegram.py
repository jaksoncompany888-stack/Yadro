"""
Telegram Provider

Telegram channel posting via Bot API.
Based on Postiz architecture patterns.
"""

import re
import asyncio
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from .base import (
    SocialProvider,
    PostResult,
    MediaItem,
    MediaType,
    ProviderError,
    AuthenticationError,
    PostingError,
    RateLimitError,
)


@dataclass
class TelegramChannel:
    """Telegram channel info."""
    id: str                    # Channel ID (e.g., "-1001234567890")
    username: Optional[str]    # Channel username without @ (e.g., "mychannel")
    title: str                 # Channel title
    description: Optional[str] = None
    member_count: Optional[int] = None


class TelegramProvider(SocialProvider):
    """
    Telegram channel posting provider.

    Uses Bot API for posting to channels where bot is admin.
    Supports text, photos, videos, documents, and media groups.

    Usage:
        provider = TelegramProvider(bot_token="123:ABC...")
        await provider.post("@mychannel", "Hello world!")
    """

    name = "telegram"
    display_name = "Telegram"

    # Telegram limits
    max_text_length = 4096
    max_caption_length = 1024
    max_media_per_post = 10

    supports_media = True
    supports_scheduling = True  # Native scheduling via send_message date param
    supports_formatting = True  # HTML formatting

    # Rate limits (Bot API: 30 messages/second to different chats)
    max_requests_per_second = 20.0

    def __init__(self, bot_token: str, bot: Any = None):
        """
        Initialize Telegram provider.

        Args:
            bot_token: Telegram Bot API token
            bot: Optional aiogram Bot instance (for reuse)
        """
        self.bot_token = bot_token
        self._bot = bot
        self._initialized = False

    async def _ensure_bot(self):
        """Lazy initialization of bot instance."""
        if self._bot is None:
            try:
                from aiogram import Bot
                self._bot = Bot(token=self.bot_token)
                self._initialized = True
            except ImportError:
                raise ProviderError("aiogram is required for Telegram provider")

    async def post(
        self,
        channel_id: str,
        text: str,
        media: Optional[List[MediaItem]] = None,
        reply_to: Optional[str] = None,
        disable_notification: bool = False,
        disable_web_preview: bool = False,
        **kwargs
    ) -> PostResult:
        """
        Post to Telegram channel.

        Args:
            channel_id: Channel ID or @username
            text: Post text (HTML formatting supported)
            media: Optional media attachments
            reply_to: Optional message ID to reply to
            disable_notification: Send silently
            disable_web_preview: Disable link previews
        """
        await self._ensure_bot()

        # Normalize channel_id
        chat_id = self._normalize_channel_id(channel_id)

        try:
            if media and len(media) > 0:
                return await self._post_with_media(
                    chat_id, text, media,
                    reply_to=reply_to,
                    disable_notification=disable_notification,
                )
            else:
                return await self._post_text(
                    chat_id, text,
                    reply_to=reply_to,
                    disable_notification=disable_notification,
                    disable_web_preview=disable_web_preview,
                )
        except Exception as e:
            error_msg = str(e)

            # Handle specific Telegram errors
            if "chat not found" in error_msg.lower():
                return PostResult.fail(
                    f"Channel not found: {channel_id}",
                    platform=self.name
                )
            elif "bot was kicked" in error_msg.lower():
                return PostResult.fail(
                    f"Bot was removed from channel: {channel_id}",
                    platform=self.name
                )
            elif "not enough rights" in error_msg.lower():
                return PostResult.fail(
                    f"Bot doesn't have posting rights in: {channel_id}",
                    platform=self.name
                )
            elif "too many requests" in error_msg.lower():
                # Extract retry_after if available
                retry_after = self._extract_retry_after(error_msg)
                raise RateLimitError(f"Rate limited: {error_msg}", retry_after)
            else:
                return PostResult.fail(error_msg, platform=self.name)

    async def _post_text(
        self,
        chat_id: str,
        text: str,
        reply_to: Optional[str] = None,
        disable_notification: bool = False,
        disable_web_preview: bool = False,
    ) -> PostResult:
        """Post text-only message."""
        # Truncate if needed
        text = self.truncate_text(text)

        message = await self._bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_notification=disable_notification,
            disable_web_page_preview=disable_web_preview,
            reply_to_message_id=int(reply_to) if reply_to else None,
        )

        return PostResult.ok(
            post_id=str(message.message_id),
            url=self._build_message_url(chat_id, message.message_id),
            platform=self.name,
            raw={"message_id": message.message_id, "chat_id": chat_id}
        )

    async def _post_with_media(
        self,
        chat_id: str,
        text: str,
        media: List[MediaItem],
        reply_to: Optional[str] = None,
        disable_notification: bool = False,
    ) -> PostResult:
        """Post message with media attachments."""
        from aiogram.types import InputMediaPhoto, InputMediaVideo, InputMediaDocument

        # Split media into chunks
        media_chunks = self.split_media(media)

        first_message_id = None

        for i, chunk in enumerate(media_chunks):
            # Build media group
            media_group = []

            for j, item in enumerate(chunk):
                # First item in first chunk gets the caption
                caption = text if (i == 0 and j == 0) else None
                if caption and len(caption) > self.max_caption_length:
                    caption = caption[:self.max_caption_length - 3] + "..."

                media_input = self._media_item_to_input(item, caption)
                if media_input:
                    media_group.append(media_input)

            if len(media_group) == 1:
                # Single media - use specific method
                message = await self._send_single_media(
                    chat_id, media_group[0], chunk[0].type,
                    disable_notification=disable_notification,
                    reply_to=reply_to if i == 0 else None,
                )
            else:
                # Media group
                messages = await self._bot.send_media_group(
                    chat_id=chat_id,
                    media=media_group,
                    disable_notification=disable_notification,
                    reply_to_message_id=int(reply_to) if (reply_to and i == 0) else None,
                )
                message = messages[0] if messages else None

            if message and first_message_id is None:
                first_message_id = message.message_id

        return PostResult.ok(
            post_id=str(first_message_id),
            url=self._build_message_url(chat_id, first_message_id),
            platform=self.name,
            raw={"message_id": first_message_id, "chat_id": chat_id}
        )

    async def _send_single_media(
        self,
        chat_id: str,
        media_input: Any,
        media_type: MediaType,
        disable_notification: bool = False,
        reply_to: Optional[str] = None,
    ):
        """Send a single media item."""
        reply_to_id = int(reply_to) if reply_to else None

        if media_type == MediaType.IMAGE:
            return await self._bot.send_photo(
                chat_id=chat_id,
                photo=media_input.media,
                caption=media_input.caption,
                parse_mode="HTML",
                disable_notification=disable_notification,
                reply_to_message_id=reply_to_id,
            )
        elif media_type == MediaType.VIDEO:
            return await self._bot.send_video(
                chat_id=chat_id,
                video=media_input.media,
                caption=media_input.caption,
                parse_mode="HTML",
                disable_notification=disable_notification,
                reply_to_message_id=reply_to_id,
            )
        elif media_type == MediaType.DOCUMENT:
            return await self._bot.send_document(
                chat_id=chat_id,
                document=media_input.media,
                caption=media_input.caption,
                parse_mode="HTML",
                disable_notification=disable_notification,
                reply_to_message_id=reply_to_id,
            )
        else:
            # Fallback to document
            return await self._bot.send_document(
                chat_id=chat_id,
                document=media_input.media,
                caption=media_input.caption,
                parse_mode="HTML",
                disable_notification=disable_notification,
                reply_to_message_id=reply_to_id,
            )

    def _media_item_to_input(self, item: MediaItem, caption: Optional[str] = None):
        """Convert MediaItem to aiogram InputMedia."""
        from aiogram.types import (
            InputMediaPhoto,
            InputMediaVideo,
            InputMediaDocument,
            InputFile,
        )

        # Get media source
        if item.file_id:
            media = item.file_id
        elif item.url:
            media = item.url
        elif item.file_path:
            media = InputFile(item.file_path)
        else:
            return None

        if item.type == MediaType.IMAGE:
            return InputMediaPhoto(media=media, caption=caption, parse_mode="HTML")
        elif item.type == MediaType.VIDEO:
            return InputMediaVideo(media=media, caption=caption, parse_mode="HTML")
        elif item.type == MediaType.DOCUMENT:
            return InputMediaDocument(media=media, caption=caption, parse_mode="HTML")
        else:
            return InputMediaDocument(media=media, caption=caption, parse_mode="HTML")

    async def validate_channel(self, channel_id: str) -> bool:
        """Check if channel exists and bot has posting permissions."""
        await self._ensure_bot()

        chat_id = self._normalize_channel_id(channel_id)

        try:
            chat = await self._bot.get_chat(chat_id)

            # Check if it's a channel or supergroup
            if chat.type not in ("channel", "supergroup"):
                return False

            # Get bot's own ID
            me = await self._bot.get_me()
            bot_id = me.id

            # Check bot's permissions
            member = await self._bot.get_chat_member(chat_id, bot_id)
            if member.status not in ("administrator", "creator"):
                return False

            # Check posting rights
            if hasattr(member, "can_post_messages"):
                return member.can_post_messages or member.status == "creator"

            return True
        except Exception:
            return False

    async def get_channel_info(self, channel_id: str) -> Optional[TelegramChannel]:
        """Get channel information."""
        await self._ensure_bot()

        chat_id = self._normalize_channel_id(channel_id)

        try:
            chat = await self._bot.get_chat(chat_id)

            return TelegramChannel(
                id=str(chat.id),
                username=chat.username,
                title=chat.title or chat.username or str(chat.id),
                description=chat.description,
                member_count=chat.member_count if hasattr(chat, "member_count") else None,
            )
        except Exception:
            return None

    async def delete_post(self, channel_id: str, post_id: str) -> bool:
        """Delete a message from channel."""
        await self._ensure_bot()

        chat_id = self._normalize_channel_id(channel_id)

        try:
            await self._bot.delete_message(chat_id, int(post_id))
            return True
        except Exception:
            return False

    async def edit_post(
        self,
        channel_id: str,
        post_id: str,
        new_text: str,
        **kwargs
    ) -> PostResult:
        """Edit an existing message."""
        await self._ensure_bot()

        chat_id = self._normalize_channel_id(channel_id)
        new_text = self.truncate_text(new_text)

        try:
            message = await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(post_id),
                text=new_text,
                parse_mode="HTML",
            )

            return PostResult.ok(
                post_id=str(message.message_id),
                url=self._build_message_url(chat_id, message.message_id),
                platform=self.name,
            )
        except Exception as e:
            return PostResult.fail(str(e), platform=self.name)

    async def schedule_post(
        self,
        channel_id: str,
        text: str,
        scheduled_time,  # datetime
        media: Optional[List[MediaItem]] = None,
        **kwargs
    ) -> PostResult:
        """
        Schedule a post for future publication.

        Note: Telegram doesn't have native scheduling via Bot API.
        This saves to drafts table for scheduler_tasks to publish later.
        """
        # For now, return error - will be implemented with scheduler integration
        return PostResult.fail(
            "Scheduling requires integration with scheduler_tasks",
            platform=self.name
        )

    async def health_check(self) -> bool:
        """Check if bot is working."""
        await self._ensure_bot()

        try:
            me = await self._bot.get_me()
            return me is not None
        except Exception:
            return False

    def format_text(self, text: str) -> str:
        """
        Convert markdown to HTML for Telegram.

        Telegram Bot API supports HTML tags:
        - <b>bold</b>
        - <i>italic</i>
        - <code>code</code>
        - <pre>preformatted</pre>
        - <a href="url">link</a>
        """
        # Convert **bold** to <b>bold</b>
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

        # Convert *italic* to <i>italic</i>
        text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)

        # Convert `code` to <code>code</code>
        text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)

        # Convert [text](url) to <a href="url">text</a>
        text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)

        return text

    def _normalize_channel_id(self, channel_id: str) -> str:
        """Normalize channel ID to format Bot API expects."""
        # Already numeric ID
        if channel_id.startswith("-"):
            return channel_id

        # Remove @ if present
        if channel_id.startswith("@"):
            return channel_id

        # Assume it's a username, add @
        return f"@{channel_id}"

    def _build_message_url(self, chat_id: str, message_id: int) -> Optional[str]:
        """Build URL to the message."""
        # Extract username from chat_id
        if chat_id.startswith("@"):
            username = chat_id[1:]
            return f"https://t.me/{username}/{message_id}"

        # For numeric IDs, we can't build a direct URL without username
        # Could query the chat to get username, but that's an extra API call
        return None

    def _extract_retry_after(self, error_msg: str) -> Optional[int]:
        """Extract retry_after seconds from rate limit error."""
        match = re.search(r'retry after (\d+)', error_msg.lower())
        if match:
            return int(match.group(1))
        return None

    async def close(self):
        """Close bot session."""
        if self._bot and hasattr(self._bot, "session"):
            await self._bot.session.close()
