"""
Yadro v0 - Telegram Bot Interface (Layer 1)

Telegram bot for user interaction.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass
from collections import defaultdict

from ..storage import Database, now_iso
from ..kernel import TaskManager, TaskLimitError
from ..llm import LLMService, Message, BudgetExceededError, LLMRateLimitError


logger = logging.getLogger(__name__)


# ==================== RATE LIMITING ====================

@dataclass
class RateLimitConfig:
    """Rate limit configuration for Telegram."""
    max_messages_per_minute: int = 10
    max_messages_per_hour: int = 60
    ban_duration_minutes: int = 5


class TelegramRateLimiter:
    """Rate limiter for Telegram messages."""
    
    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()
        self._messages: Dict[int, List[datetime]] = defaultdict(list)
        self._bans: Dict[int, datetime] = {}
    
    def check(self, user_id: int) -> tuple:
        """Check if user can send message. Returns (allowed, error_message)."""
        now = datetime.now(timezone.utc)
        
        if user_id in self._bans:
            if now < self._bans[user_id]:
                remaining = (self._bans[user_id] - now).seconds // 60
                return False, f"Слишком много сообщений. Подожди {remaining + 1} мин."
            else:
                del self._bans[user_id]
        
        self._cleanup(user_id)
        messages = self._messages[user_id]
        
        minute_ago = now - timedelta(minutes=1)
        per_minute = sum(1 for ts in messages if ts > minute_ago)
        
        if per_minute >= self.config.max_messages_per_minute:
            self._ban(user_id)
            return False, "Слишком быстро! Подожди минутку."
        
        hour_ago = now - timedelta(hours=1)
        per_hour = sum(1 for ts in messages if ts > hour_ago)
        
        if per_hour >= self.config.max_messages_per_hour:
            self._ban(user_id)
            return False, "Лимит сообщений на час исчерпан."
        
        return True, None
    
    def record(self, user_id: int) -> None:
        """Record a message."""
        self._messages[user_id].append(datetime.now(timezone.utc))
    
    def _ban(self, user_id: int) -> None:
        """Ban user temporarily."""
        self._bans[user_id] = datetime.now(timezone.utc) + timedelta(
            minutes=self.config.ban_duration_minutes
        )
    
    def _cleanup(self, user_id: int) -> None:
        """Remove old entries."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        self._messages[user_id] = [
            ts for ts in self._messages[user_id] if ts > cutoff
        ]


# ==================== USER WHITELIST ====================

class UserWhitelist:
    """Manages allowed users."""
    
    def __init__(self, db: Database, allow_all: bool = False):
        self._db = db
        self._allow_all = allow_all
        self._cache: Dict[int, bool] = {}
    
    def is_allowed(self, tg_id: int) -> bool:
        """Check if Telegram user is allowed."""
        if self._allow_all:
            return True
        
        if tg_id in self._cache:
            return self._cache[tg_id]
        
        row = self._db.fetch_one(
            "SELECT id, is_active FROM users WHERE tg_id = ?",
            (tg_id,)
        )
        
        allowed = row is not None and row["is_active"] == 1
        self._cache[tg_id] = allowed
        return allowed
    
    def add_user(self, tg_id: int, username: Optional[str] = None) -> int:
        """Add user to whitelist."""
        user_id = self._db.execute(
            """INSERT INTO users (tg_id, username, is_active, created_at)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(tg_id) DO UPDATE SET is_active = 1, username = ?""",
            (tg_id, username, now_iso(), username)
        )
        self._cache[tg_id] = True
        return user_id
    
    def remove_user(self, tg_id: int) -> None:
        """Remove user from whitelist."""
        self._db.execute(
            "UPDATE users SET is_active = 0 WHERE tg_id = ?",
            (tg_id,)
        )
        self._cache[tg_id] = False
    
    def clear_cache(self) -> None:
        """Clear the cache."""
        self._cache.clear()


# ==================== MESSAGE MODELS ====================

@dataclass
class TelegramMessage:
    """Incoming Telegram message."""
    message_id: int
    chat_id: int
    user_id: int
    username: Optional[str]
    text: str
    is_command: bool = False
    command: Optional[str] = None
    command_args: Optional[str] = None


@dataclass
class TelegramResponse:
    """Outgoing response."""
    text: str
    chat_id: int
    reply_to: Optional[int] = None
    parse_mode: str = "Markdown"


# ==================== BOT HANDLER ====================

class TelegramBotHandler:
    """Handles Telegram bot logic."""
    
    def __init__(
        self,
        db: Database,
        task_manager: Optional[TaskManager] = None,
        llm_service: Optional[LLMService] = None,
        rate_limiter: Optional[TelegramRateLimiter] = None,
        whitelist: Optional[UserWhitelist] = None,
    ):
        self._db = db
        self._task_manager = task_manager or TaskManager(db=db)
        self._llm_service = llm_service or LLMService(db=db)
        self._rate_limiter = rate_limiter or TelegramRateLimiter()
        self._whitelist = whitelist or UserWhitelist(db=db)
        
        self._commands: Dict[str, Callable] = {
            "start": self._cmd_start,
            "help": self._cmd_help,
            "status": self._cmd_status,
            "tasks": self._cmd_tasks,
            "cancel": self._cmd_cancel,
        }
    
    async def handle_message(self, msg: TelegramMessage) -> TelegramResponse:
        """Handle incoming message."""
        if not self._whitelist.is_allowed(msg.user_id):
            return TelegramResponse(
                text="⛔ Доступ запрещён.",
                chat_id=msg.chat_id,
            )
        
        allowed, error = self._rate_limiter.check(msg.user_id)
        if not allowed:
            return TelegramResponse(text=f"⏳ {error}", chat_id=msg.chat_id)
        
        self._rate_limiter.record(msg.user_id)
        user_id = self._get_or_create_user(msg.user_id, msg.username)
        
        if msg.is_command and msg.command in self._commands:
            return await self._commands[msg.command](msg, user_id)
        else:
            return await self._handle_text(msg, user_id)
    
    def _get_or_create_user(self, tg_id: int, username: Optional[str]) -> int:
        """Get or create internal user ID."""
        row = self._db.fetch_one(
            "SELECT id FROM users WHERE tg_id = ?", (tg_id,)
        )
        if row:
            return row["id"]
        return self._db.execute(
            "INSERT INTO users (tg_id, username, created_at) VALUES (?, ?, ?)",
            (tg_id, username, now_iso())
        )
    
    async def _handle_text(self, msg: TelegramMessage, user_id: int) -> TelegramResponse:
        """Handle regular text message."""
        try:
            task = self._task_manager.enqueue(
                user_id=user_id,
                input_text=msg.text,
                task_type="general",
            )
            
            response = self._llm_service.complete_simple(
                prompt=msg.text,
                user_id=user_id,
                task_id=task.id,
            )
            
            self._task_manager.claim()
            self._task_manager.succeed(task.id, result=response)
            
            return TelegramResponse(
                text=response,
                chat_id=msg.chat_id,
                reply_to=msg.message_id,
            )
            
        except TaskLimitError as e:
            return TelegramResponse(
                text=f"⚠️ {str(e)}",
                chat_id=msg.chat_id,
            )
        except BudgetExceededError as e:
            return TelegramResponse(
                text=f"💰 Лимит бюджета: {str(e)}",
                chat_id=msg.chat_id,
            )
        except LLMRateLimitError as e:
            return TelegramResponse(
                text=f"⏳ {str(e)}",
                chat_id=msg.chat_id,
            )
        except Exception as e:
            logger.exception("Error handling message")
            return TelegramResponse(
                text=f"❌ Ошибка: {type(e).__name__}",
                chat_id=msg.chat_id,
            )
    
    async def _cmd_start(self, msg: TelegramMessage, user_id: int) -> TelegramResponse:
        """Handle /start command."""
        return TelegramResponse(
            text="""👋 Привет! Я Yadro — твой AI-ассистент.

Просто напиши что нужно сделать.

Команды: /help /status /tasks""",
            chat_id=msg.chat_id,
        )
    
    async def _cmd_help(self, msg: TelegramMessage, user_id: int) -> TelegramResponse:
        """Handle /help command."""
        return TelegramResponse(
            text="""📖 **Справка**

/start — начало
/help — справка
/status — лимиты
/tasks — задачи
/cancel [id] — отменить""",
            chat_id=msg.chat_id,
        )
    
    async def _cmd_status(self, msg: TelegramMessage, user_id: int) -> TelegramResponse:
        """Handle /status command."""
        task_limits = self._task_manager.get_user_limits_status(user_id)
        llm_limits = self._llm_service.get_user_limits_status(user_id)
        
        return TelegramResponse(
            text=f"""📊 **Статус**

Задачи: {task_limits['active']['used']}/{task_limits['active']['limit']}
LLM запросы/мин: {llm_limits['requests_per_minute']['used']}/{llm_limits['requests_per_minute']['limit']}
Потрачено/час: ${llm_limits['cost_per_hour']['used']:.3f}""",
            chat_id=msg.chat_id,
        )
    
    async def _cmd_tasks(self, msg: TelegramMessage, user_id: int) -> TelegramResponse:
        """Handle /tasks command."""
        tasks = self._task_manager.list_by_user(user_id, limit=5)
        
        if not tasks:
            return TelegramResponse(text="📋 Нет задач.", chat_id=msg.chat_id)
        
        lines = ["📋 **Задачи:**"]
        for task in tasks:
            emoji = {"queued": "⏳", "running": "🔄", "paused": "⏸️",
                     "succeeded": "✅", "failed": "❌"}.get(task.status.value, "❓")
            text = (task.input_text or "")[:25] + "..." if len(task.input_text or "") > 25 else task.input_text
            lines.append(f"{emoji} `{task.id}`: {text}")
        
        return TelegramResponse(text="\n".join(lines), chat_id=msg.chat_id)
    
    async def _cmd_cancel(self, msg: TelegramMessage, user_id: int) -> TelegramResponse:
        """Handle /cancel command."""
        if not msg.command_args:
            return TelegramResponse(text="Укажи ID: `/cancel 123`", chat_id=msg.chat_id)
        
        try:
            task_id = int(msg.command_args.strip())
            task = self._task_manager.get(task_id)
            
            if not task:
                return TelegramResponse(text=f"❌ Задача {task_id} не найдена.", chat_id=msg.chat_id)
            if task.user_id != user_id:
                return TelegramResponse(text="⛔ Не твоя задача.", chat_id=msg.chat_id)
            
            self._task_manager.cancel(task_id)
            return TelegramResponse(text=f"🚫 Задача {task_id} отменена.", chat_id=msg.chat_id)
        except ValueError:
            return TelegramResponse(text="❌ Неверный ID.", chat_id=msg.chat_id)


def parse_telegram_message(update: Dict) -> Optional[TelegramMessage]:
    """Parse Telegram update into TelegramMessage."""
    message = update.get("message", {})
    
    if not message or "text" not in message:
        return None
    
    text = message["text"]
    is_command = text.startswith("/")
    command = None
    command_args = None
    
    if is_command:
        parts = text[1:].split(maxsplit=1)
        command = parts[0].lower().split("@")[0]
        command_args = parts[1] if len(parts) > 1 else None
    
    return TelegramMessage(
        message_id=message["message_id"],
        chat_id=message["chat"]["id"],
        user_id=message["from"]["id"],
        username=message["from"].get("username"),
        text=text,
        is_command=is_command,
        command=command,
        command_args=command_args,
    )
