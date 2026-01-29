"""
User Channels API

Управление каналами пользователя для автопостинга.
Отличие от channels.py (анализ конкурентов):
- Это СВОИ каналы куда постим
- Требуется валидация что бот добавлен как админ
"""

import os
from typing import List, Optional
from pydantic import BaseModel

from fastapi import APIRouter, HTTPException, Depends

from app.providers import TelegramProvider, Platform
from .deps import get_current_user


router = APIRouter(prefix="/user-channels", tags=["user-channels"])


# =============================================================================
# Models
# =============================================================================

class UserChannelCreate(BaseModel):
    """Добавление канала для постинга."""
    platform: str = "telegram"  # telegram или vk
    channel_id: str  # @username или ID


class UserChannelInfo(BaseModel):
    """Информация о канале пользователя."""
    platform: str
    channel_id: str
    name: str
    username: Optional[str] = None
    subscribers: int = 0
    is_valid: bool = True
    can_post: bool = True


class ValidateResponse(BaseModel):
    """Результат валидации канала."""
    valid: bool
    can_post: bool
    error: Optional[str] = None
    channel_info: Optional[UserChannelInfo] = None


# In-memory storage (TODO: сохранять в БД)
user_posting_channels: dict = {}  # user_id -> [UserChannelInfo]


# =============================================================================
# Endpoints
# =============================================================================

@router.get("", response_model=List[UserChannelInfo])
async def list_user_channels(user: dict = Depends(get_current_user)):
    """
    Список каналов пользователя для постинга.
    """
    user_id = user["id"]
    return user_posting_channels.get(user_id, [])


@router.post("/add", response_model=ValidateResponse)
async def add_user_channel(
    data: UserChannelCreate,
    user: dict = Depends(get_current_user)
):
    """
    Добавить канал для постинга.

    Проверяет что бот добавлен в канал как админ с правами постинга.
    """
    user_id = user["id"]

    # Нормализуем channel_id
    channel_id = data.channel_id.strip()
    if not channel_id.startswith("@") and not channel_id.startswith("-"):
        channel_id = f"@{channel_id}"

    if data.platform == "telegram":
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            raise HTTPException(status_code=500, detail="Bot not configured")

        provider = TelegramProvider(bot_token=bot_token)

        try:
            # Проверяем что бот может постить в канал
            is_valid = await provider.validate_channel(channel_id)

            if not is_valid:
                await provider.close()
                return ValidateResponse(
                    valid=False,
                    can_post=False,
                    error="Бот не добавлен в канал как администратор. Добавьте бота @YadroSMMBot в канал с правами на публикацию."
                )

            # Получаем информацию о канале
            channel_info = await provider.get_channel_info(channel_id)
            await provider.close()

            if not channel_info:
                return ValidateResponse(
                    valid=False,
                    can_post=False,
                    error="Не удалось получить информацию о канале"
                )

            # Создаём запись
            user_channel = UserChannelInfo(
                platform="telegram",
                channel_id=channel_id,
                name=channel_info.title,
                username=channel_info.username,
                subscribers=channel_info.members_count or 0,
                is_valid=True,
                can_post=True,
            )

            # Сохраняем
            if user_id not in user_posting_channels:
                user_posting_channels[user_id] = []

            # Проверяем дубликат
            existing = [c for c in user_posting_channels[user_id] if c.channel_id == channel_id]
            if not existing:
                user_posting_channels[user_id].append(user_channel)

            return ValidateResponse(
                valid=True,
                can_post=True,
                channel_info=user_channel
            )

        except Exception as e:
            await provider.close()
            error_msg = str(e)

            if "Chat not found" in error_msg:
                error_msg = "Канал не найден. Проверьте username."
            elif "bot was kicked" in error_msg or "Forbidden" in error_msg:
                error_msg = "Бот удалён из канала или не имеет прав."
            elif "CHAT_ADMIN_REQUIRED" in error_msg:
                error_msg = "Бот не является администратором канала."

            return ValidateResponse(
                valid=False,
                can_post=False,
                error=error_msg
            )

    elif data.platform == "vk":
        # TODO: VK implementation
        raise HTTPException(status_code=501, detail="VK пока не поддерживается")

    else:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {data.platform}")


@router.delete("/{channel_id}")
async def remove_user_channel(
    channel_id: str,
    user: dict = Depends(get_current_user)
):
    """Удалить канал из списка постинга."""
    user_id = user["id"]

    # Декодируем channel_id (@ может быть закодирован как %40)
    from urllib.parse import unquote
    channel_id = unquote(channel_id)

    if user_id in user_posting_channels:
        user_posting_channels[user_id] = [
            c for c in user_posting_channels[user_id]
            if c.channel_id != channel_id and c.channel_id != f"@{channel_id}"
        ]

    return {"status": "removed", "channel_id": channel_id}


@router.post("/validate", response_model=ValidateResponse)
async def validate_channel(
    data: UserChannelCreate,
    user: dict = Depends(get_current_user)
):
    """
    Проверить что бот может постить в канал (без добавления).
    """
    # Просто вызываем add но не сохраняем
    channel_id = data.channel_id.strip()
    if not channel_id.startswith("@") and not channel_id.startswith("-"):
        channel_id = f"@{channel_id}"

    if data.platform == "telegram":
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            raise HTTPException(status_code=500, detail="Bot not configured")

        provider = TelegramProvider(bot_token=bot_token)

        try:
            is_valid = await provider.validate_channel(channel_id)
            channel_info = await provider.get_channel_info(channel_id) if is_valid else None
            await provider.close()

            if is_valid and channel_info:
                return ValidateResponse(
                    valid=True,
                    can_post=True,
                    channel_info=UserChannelInfo(
                        platform="telegram",
                        channel_id=channel_id,
                        name=channel_info.title,
                        username=channel_info.username,
                        subscribers=channel_info.members_count or 0,
                        is_valid=True,
                        can_post=True,
                    )
                )
            else:
                return ValidateResponse(
                    valid=False,
                    can_post=False,
                    error="Бот не может постить в этот канал"
                )
        except Exception as e:
            await provider.close()
            return ValidateResponse(
                valid=False,
                can_post=False,
                error=str(e)
            )

    raise HTTPException(status_code=400, detail="Unsupported platform")
