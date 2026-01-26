"""
Tests for Layer 1: Telegram Interface
"""
import pytest
from app.storage import Database
from app.kernel import TaskManager
from app.llm import LLMService
from app.interfaces import (
    TelegramBotHandler,
    TelegramMessage,
    TelegramResponse,
    TelegramRateLimiter,
    RateLimitConfig,
    UserWhitelist,
    parse_telegram_message,
)


class TestTelegramRateLimiter:
    """Tests for TelegramRateLimiter."""
    
    @pytest.fixture
    def limiter(self):
        config = RateLimitConfig(max_messages_per_minute=3, max_messages_per_hour=10)
        return TelegramRateLimiter(config)
    
    def test_allows_first_message(self, limiter):
        allowed, error = limiter.check(user_id=1)
        assert allowed is True
        assert error is None
    
    def test_rate_limit_per_minute(self, limiter):
        for _ in range(3):
            limiter.check(user_id=1)
            limiter.record(user_id=1)
        
        allowed, error = limiter.check(user_id=1)
        assert allowed is False
        assert error is not None
    
    def test_separate_users(self, limiter):
        for _ in range(3):
            limiter.record(user_id=1)
        
        allowed, _ = limiter.check(user_id=2)
        assert allowed is True


class TestUserWhitelist:
    """Tests for UserWhitelist."""
    
    @pytest.fixture
    def db(self, tmp_path):
        return Database(tmp_path / "test.sqlite3")
    
    @pytest.fixture
    def whitelist(self, db):
        return UserWhitelist(db=db)
    
    def test_unknown_user_not_allowed(self, whitelist):
        assert whitelist.is_allowed(tg_id=99999) is False
    
    def test_add_user(self, whitelist):
        whitelist.add_user(tg_id=123, username="test")
        assert whitelist.is_allowed(tg_id=123) is True
    
    def test_remove_user(self, whitelist):
        whitelist.add_user(tg_id=123)
        whitelist.remove_user(tg_id=123)
        assert whitelist.is_allowed(tg_id=123) is False
    
    def test_allow_all_mode(self, db):
        whitelist = UserWhitelist(db=db, allow_all=True)
        assert whitelist.is_allowed(tg_id=99999) is True


class TestParseMessage:
    """Tests for parse_telegram_message."""
    
    def test_parse_text_message(self):
        update = {
            "message": {
                "message_id": 1,
                "chat": {"id": 100},
                "from": {"id": 200, "username": "test"},
                "text": "Hello",
            }
        }
        msg = parse_telegram_message(update)
        
        assert msg.message_id == 1
        assert msg.chat_id == 100
        assert msg.user_id == 200
        assert msg.text == "Hello"
        assert msg.is_command is False
    
    def test_parse_command(self):
        update = {
            "message": {
                "message_id": 1,
                "chat": {"id": 100},
                "from": {"id": 200},
                "text": "/start",
            }
        }
        msg = parse_telegram_message(update)
        
        assert msg.is_command is True
        assert msg.command == "start"
    
    def test_parse_command_with_args(self):
        update = {
            "message": {
                "message_id": 1,
                "chat": {"id": 100},
                "from": {"id": 200},
                "text": "/cancel 123",
            }
        }
        msg = parse_telegram_message(update)
        
        assert msg.command == "cancel"
        assert msg.command_args == "123"
    
    def test_parse_empty_update(self):
        msg = parse_telegram_message({})
        assert msg is None


class TestTelegramBotHandler:
    """Tests for TelegramBotHandler."""
    
    @pytest.fixture
    def db(self, tmp_path):
        return Database(tmp_path / "test.sqlite3")
    
    @pytest.fixture
    def handler(self, db):
        db.execute(
            "INSERT INTO users (tg_id, username, is_active) VALUES (?, ?, 1)",
            (123, "test")
        )
        return TelegramBotHandler(
            db=db,
            task_manager=TaskManager(db=db),
            llm_service=LLMService(db=db, mock_mode=True),
        )
    
    @pytest.mark.asyncio
    async def test_handle_start(self, handler):
        msg = TelegramMessage(
            message_id=1, chat_id=100, user_id=123,
            username="test", text="/start",
            is_command=True, command="start",
        )
        response = await handler.handle_message(msg)
        
        assert "Привет" in response.text
    
    @pytest.mark.asyncio
    async def test_handle_help(self, handler):
        msg = TelegramMessage(
            message_id=1, chat_id=100, user_id=123,
            username="test", text="/help",
            is_command=True, command="help",
        )
        response = await handler.handle_message(msg)
        
        assert "Справка" in response.text
    
    @pytest.mark.asyncio
    async def test_handle_status(self, handler):
        msg = TelegramMessage(
            message_id=1, chat_id=100, user_id=123,
            username="test", text="/status",
            is_command=True, command="status",
        )
        response = await handler.handle_message(msg)
        
        assert "Статус" in response.text
    
    @pytest.mark.asyncio
    async def test_handle_text(self, handler):
        msg = TelegramMessage(
            message_id=1, chat_id=100, user_id=123,
            username="test", text="Hello",
            is_command=False,
        )
        response = await handler.handle_message(msg)
        
        assert response.text is not None
        assert len(response.text) > 0
    
    @pytest.mark.asyncio
    async def test_unauthorized_user(self, handler):
        msg = TelegramMessage(
            message_id=1, chat_id=100, user_id=99999,
            username="hacker", text="Hello",
        )
        response = await handler.handle_message(msg)
        
        assert "Доступ запрещён" in response.text
    
    @pytest.mark.asyncio
    async def test_cancel_without_id(self, handler):
        msg = TelegramMessage(
            message_id=1, chat_id=100, user_id=123,
            username="test", text="/cancel",
            is_command=True, command="cancel",
        )
        response = await handler.handle_message(msg)
        
        assert "Укажи ID" in response.text
