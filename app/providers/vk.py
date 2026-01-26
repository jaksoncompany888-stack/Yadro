"""
VK Provider

VKontakte posting via VK API.
Based on Postiz architecture patterns.

OAuth2 flow:
1. User opens VK auth URL
2. Grants permissions
3. Redirect with code
4. Exchange code for access_token
5. Store token (refresh via VK's long-lived tokens)
"""

import re
import asyncio
import hashlib
import base64
import secrets
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

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
class VKToken:
    """VK OAuth token."""
    access_token: str
    user_id: int
    expires_in: Optional[int] = None  # VK tokens can be long-lived
    created_at: datetime = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()


@dataclass
class VKGroup:
    """VK group/community info."""
    id: int                    # Group ID (positive number)
    screen_name: str           # Short name (e.g., "club123")
    name: str                  # Full name
    description: Optional[str] = None
    member_count: Optional[int] = None
    is_admin: bool = False
    can_post: bool = False


class VKProvider(SocialProvider):
    """
    VKontakte community posting provider.

    Uses VK API for posting to groups/communities where user is admin.
    Supports text, photos, videos, documents, and polls.

    OAuth2 + PKCE flow for authentication.

    Usage:
        provider = VKProvider(app_id="123", app_secret="...")

        # 1. Get auth URL
        auth_url, state = provider.get_auth_url(redirect_uri)

        # 2. User authorizes, you get code
        token = await provider.exchange_code(code, redirect_uri)

        # 3. Post to group
        provider.set_token(token)
        result = await provider.post("-123456", "Hello VK!")
    """

    name = "vk"
    display_name = "ВКонтакте"

    # VK limits
    max_text_length = 15895  # VK allows long posts
    max_media_per_post = 10

    supports_media = True
    supports_scheduling = True  # Via publish_date param
    supports_formatting = False  # VK has limited formatting

    # VK rate limits: 3 requests per second
    max_requests_per_second = 3.0
    max_parallel_tasks = 2  # Like Postiz

    # VK API
    API_VERSION = "5.199"
    API_BASE = "https://api.vk.com/method"
    OAUTH_URL = "https://oauth.vk.com/authorize"
    TOKEN_URL = "https://oauth.vk.com/access_token"

    # Required permissions (scope)
    SCOPE = [
        "wall",        # Post to wall
        "photos",      # Upload photos
        "video",       # Upload videos
        "docs",        # Upload documents
        "groups",      # Manage groups
        "offline",     # Long-lived token
    ]

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        access_token: Optional[str] = None,
    ):
        """
        Initialize VK provider.

        Args:
            app_id: VK app ID
            app_secret: VK app secret
            access_token: Optional pre-existing access token
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: Optional[VKToken] = None

        if access_token:
            self._token = VKToken(access_token=access_token, user_id=0)

        self._pkce_verifier: Optional[str] = None

    # =========================================================================
    # OAuth2 + PKCE
    # =========================================================================

    def get_auth_url(self, redirect_uri: str, state: Optional[str] = None) -> tuple[str, str]:
        """
        Generate VK OAuth authorization URL.

        Args:
            redirect_uri: URL to redirect after auth
            state: Optional state parameter for CSRF protection

        Returns:
            Tuple of (auth_url, state)
        """
        if state is None:
            state = secrets.token_urlsafe(32)

        # Generate PKCE verifier and challenge
        self._pkce_verifier = secrets.token_urlsafe(64)
        challenge = self._generate_pkce_challenge(self._pkce_verifier)

        params = {
            "client_id": self.app_id,
            "redirect_uri": redirect_uri,
            "scope": ",".join(self.SCOPE),
            "response_type": "code",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "v": self.API_VERSION,
        }

        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.OAUTH_URL}?{query}", state

    async def exchange_code(self, code: str, redirect_uri: str) -> VKToken:
        """
        Exchange authorization code for access token.

        Args:
            code: Authorization code from redirect
            redirect_uri: Same redirect_uri used in get_auth_url

        Returns:
            VKToken with access_token
        """
        import aiohttp

        if not self._pkce_verifier:
            raise AuthenticationError("PKCE verifier not found. Call get_auth_url first.")

        params = {
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
            "code_verifier": self._pkce_verifier,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(self.TOKEN_URL, params=params) as resp:
                data = await resp.json()

                if "error" in data:
                    raise AuthenticationError(f"VK auth error: {data.get('error_description', data['error'])}")

                self._token = VKToken(
                    access_token=data["access_token"],
                    user_id=data["user_id"],
                    expires_in=data.get("expires_in"),
                )
                return self._token

    def set_token(self, token: VKToken):
        """Set access token for API calls."""
        self._token = token

    def _generate_pkce_challenge(self, verifier: str) -> str:
        """Generate PKCE code challenge from verifier."""
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    # =========================================================================
    # API Methods
    # =========================================================================

    async def _api_call(self, method: str, **params) -> Dict[str, Any]:
        """
        Make VK API call.

        Args:
            method: API method name (e.g., "wall.post")
            **params: Method parameters

        Returns:
            API response dict
        """
        import aiohttp

        if not self._token:
            raise AuthenticationError("No access token. Call exchange_code first.")

        url = f"{self.API_BASE}/{method}"
        params["access_token"] = self._token.access_token
        params["v"] = self.API_VERSION

        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=params) as resp:
                data = await resp.json()

                if "error" in data:
                    error = data["error"]
                    code = error.get("error_code", 0)
                    msg = error.get("error_msg", "Unknown error")

                    if code == 6:  # Too many requests
                        raise RateLimitError(f"VK rate limit: {msg}", retry_after=1)
                    elif code == 5:  # Auth error
                        raise AuthenticationError(f"VK auth error: {msg}")
                    else:
                        raise ProviderError(f"VK API error {code}: {msg}")

                return data.get("response", data)

    async def post(
        self,
        channel_id: str,
        text: str,
        media: Optional[List[MediaItem]] = None,
        reply_to: Optional[str] = None,
        **kwargs
    ) -> PostResult:
        """
        Post to VK group wall.

        Args:
            channel_id: Group ID (negative number like "-123456" or just "123456")
            text: Post text
            media: Optional media attachments
            reply_to: Not used in VK
        """
        # Normalize group ID to negative (owner_id format)
        owner_id = self._normalize_group_id(channel_id)

        try:
            # Upload media if present
            attachments = []
            if media:
                attachments = await self._upload_media(owner_id, media)

            # Create post
            params = {
                "owner_id": owner_id,
                "message": text,
                "from_group": 1,  # Post as group, not as user
            }

            if attachments:
                params["attachments"] = ",".join(attachments)

            result = await self._api_call("wall.post", **params)
            post_id = result.get("post_id")

            return PostResult.ok(
                post_id=str(post_id),
                url=f"https://vk.com/wall{owner_id}_{post_id}",
                platform=self.name,
                raw=result,
            )
        except ProviderError as e:
            return PostResult.fail(str(e), platform=self.name)
        except Exception as e:
            return PostResult.fail(f"VK posting error: {str(e)}", platform=self.name)

    async def _upload_media(self, owner_id: int, media: List[MediaItem]) -> List[str]:
        """
        Upload media to VK servers.

        VK requires uploading media before attaching to post.
        Flow: get_upload_url → upload file → save_photo/video

        Returns:
            List of attachment strings (e.g., ["photo-123_456", "video-123_789"])
        """
        import aiohttp

        attachments = []

        for item in media[:self.max_media_per_post]:
            try:
                if item.type == MediaType.IMAGE:
                    attachment = await self._upload_photo(owner_id, item)
                elif item.type == MediaType.VIDEO:
                    attachment = await self._upload_video(owner_id, item)
                elif item.type == MediaType.DOCUMENT:
                    attachment = await self._upload_document(owner_id, item)
                else:
                    continue  # Skip unsupported types

                if attachment:
                    attachments.append(attachment)
            except Exception as e:
                # Log but continue with other media
                print(f"VK media upload error: {e}")
                continue

        return attachments

    async def _upload_photo(self, owner_id: int, item: MediaItem) -> Optional[str]:
        """Upload photo to VK."""
        import aiohttp

        # 1. Get upload URL
        upload_server = await self._api_call(
            "photos.getWallUploadServer",
            group_id=abs(owner_id),
        )
        upload_url = upload_server["upload_url"]

        # 2. Upload file
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()

            if item.file_path:
                with open(item.file_path, "rb") as f:
                    form.add_field("photo", f, filename="photo.jpg")
                    async with session.post(upload_url, data=form) as resp:
                        upload_result = await resp.json()
            elif item.url:
                # Download and re-upload
                async with session.get(item.url) as resp:
                    content = await resp.read()
                form.add_field("photo", content, filename="photo.jpg")
                async with session.post(upload_url, data=form) as resp:
                    upload_result = await resp.json()
            else:
                return None

        # 3. Save photo
        saved = await self._api_call(
            "photos.saveWallPhoto",
            group_id=abs(owner_id),
            photo=upload_result["photo"],
            server=upload_result["server"],
            hash=upload_result["hash"],
        )

        if saved:
            photo = saved[0]
            return f"photo{photo['owner_id']}_{photo['id']}"

        return None

    async def _upload_video(self, owner_id: int, item: MediaItem) -> Optional[str]:
        """Upload video to VK."""
        # VK video upload is more complex - requires save first, then upload
        # Simplified implementation
        save_result = await self._api_call(
            "video.save",
            group_id=abs(owner_id),
            name=item.caption or "Video",
        )

        upload_url = save_result["upload_url"]

        import aiohttp
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()

            if item.file_path:
                with open(item.file_path, "rb") as f:
                    form.add_field("video_file", f, filename="video.mp4")
                    async with session.post(upload_url, data=form) as resp:
                        await resp.json()
            elif item.url:
                async with session.get(item.url) as resp:
                    content = await resp.read()
                form.add_field("video_file", content, filename="video.mp4")
                async with session.post(upload_url, data=form) as resp:
                    await resp.json()

        return f"video{save_result['owner_id']}_{save_result['video_id']}"

    async def _upload_document(self, owner_id: int, item: MediaItem) -> Optional[str]:
        """Upload document to VK."""
        # Get upload URL
        upload_server = await self._api_call(
            "docs.getWallUploadServer",
            group_id=abs(owner_id),
        )
        upload_url = upload_server["upload_url"]

        import aiohttp
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()

            if item.file_path:
                with open(item.file_path, "rb") as f:
                    form.add_field("file", f)
                    async with session.post(upload_url, data=form) as resp:
                        upload_result = await resp.json()
            elif item.url:
                async with session.get(item.url) as resp:
                    content = await resp.read()
                form.add_field("file", content)
                async with session.post(upload_url, data=form) as resp:
                    upload_result = await resp.json()
            else:
                return None

        # Save document
        saved = await self._api_call(
            "docs.save",
            file=upload_result["file"],
        )

        if saved and "doc" in saved:
            doc = saved["doc"]
            return f"doc{doc['owner_id']}_{doc['id']}"

        return None

    async def validate_channel(self, channel_id: str) -> bool:
        """Check if user can post to this group."""
        try:
            group_id = abs(self._normalize_group_id(channel_id))

            # Get group info with admin status
            groups = await self._api_call(
                "groups.getById",
                group_id=group_id,
                fields="can_post,is_admin",
            )

            if groups and len(groups) > 0:
                group = groups[0]
                return group.get("is_admin", 0) == 1 or group.get("can_post", 0) == 1

            return False
        except Exception:
            return False

    async def get_managed_groups(self) -> List[VKGroup]:
        """Get list of groups where user is admin."""
        result = await self._api_call(
            "groups.get",
            extended=1,
            filter="admin,editor",
            fields="description,members_count,can_post",
        )

        groups = []
        for item in result.get("items", []):
            groups.append(VKGroup(
                id=item["id"],
                screen_name=item.get("screen_name", f"club{item['id']}"),
                name=item["name"],
                description=item.get("description"),
                member_count=item.get("members_count"),
                is_admin=True,
                can_post=item.get("can_post", 0) == 1,
            ))

        return groups

    async def delete_post(self, channel_id: str, post_id: str) -> bool:
        """Delete a post from group wall."""
        try:
            owner_id = self._normalize_group_id(channel_id)
            await self._api_call(
                "wall.delete",
                owner_id=owner_id,
                post_id=int(post_id),
            )
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
        """Edit an existing post."""
        try:
            owner_id = self._normalize_group_id(channel_id)

            await self._api_call(
                "wall.edit",
                owner_id=owner_id,
                post_id=int(post_id),
                message=new_text,
            )

            return PostResult.ok(
                post_id=post_id,
                url=f"https://vk.com/wall{owner_id}_{post_id}",
                platform=self.name,
            )
        except Exception as e:
            return PostResult.fail(str(e), platform=self.name)

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

        VK supports native scheduling via publish_date parameter.
        """
        owner_id = self._normalize_group_id(channel_id)

        try:
            attachments = []
            if media:
                attachments = await self._upload_media(owner_id, media)

            params = {
                "owner_id": owner_id,
                "message": text,
                "from_group": 1,
                "publish_date": int(scheduled_time.timestamp()),
            }

            if attachments:
                params["attachments"] = ",".join(attachments)

            result = await self._api_call("wall.post", **params)
            post_id = result.get("post_id")

            return PostResult.ok(
                post_id=str(post_id),
                url=f"https://vk.com/wall{owner_id}_{post_id}",
                platform=self.name,
                raw=result,
            )
        except Exception as e:
            return PostResult.fail(str(e), platform=self.name)

    async def health_check(self) -> bool:
        """Check if token is valid."""
        try:
            await self._api_call("users.get")
            return True
        except Exception:
            return False

    def _normalize_group_id(self, channel_id: str) -> int:
        """
        Normalize group ID to negative owner_id format.

        VK uses negative IDs for groups in many API methods.
        """
        # Remove 'club' or 'public' prefix if present
        channel_id = str(channel_id).lower()
        channel_id = re.sub(r'^(club|public)', '', channel_id)

        # Remove @ if present
        channel_id = channel_id.lstrip("@-")

        # Parse to int and make negative
        group_id = int(channel_id)
        return -abs(group_id)
