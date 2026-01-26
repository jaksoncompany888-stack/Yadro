"""
Tests for Layer 5: LLM Service

Run with: pytest -q
"""
import pytest
from datetime import datetime, timezone, timedelta

from app.storage import Database
from app.llm import (
    LLMService, LLMResponse, LLMRequest, Message, MessageRole,
    LLMProvider, ModelConfig, MODELS,
    PromptBuilder, ModelRouter, RouterConfig,
    CostTracker, UsageSummary,
    LLMServiceConfig, LLMRateLimiter,
    BudgetExceededError, TokenLimitError, LLMRateLimitError,
)


class TestMessage:
    """Tests for Message class."""
    
    def test_create_system_message(self):
        """Test creating system message."""
        msg = Message.system("You are helpful")
        
        assert msg.role == MessageRole.SYSTEM
        assert msg.content == "You are helpful"
    
    def test_create_user_message(self):
        """Test creating user message."""
        msg = Message.user("Hello")
        
        assert msg.role == MessageRole.USER
        assert msg.content == "Hello"
    
    def test_create_assistant_message(self):
        """Test creating assistant message."""
        msg = Message.assistant("Hi there")
        
        assert msg.role == MessageRole.ASSISTANT
    
    def test_to_dict(self):
        """Test message serialization."""
        msg = Message.user("Test")
        data = msg.to_dict()
        
        assert data["role"] == "user"
        assert data["content"] == "Test"


class TestModelConfig:
    """Tests for ModelConfig."""
    
    def test_calculate_cost(self):
        """Test cost calculation."""
        config = ModelConfig(
            name="test",
            provider=LLMProvider.OPENAI,
            input_price_per_million=2.50,
            output_price_per_million=10.00,
        )
        
        # 1M input + 1M output = $2.50 + $10.00 = $12.50
        cost = config.calculate_cost(1_000_000, 1_000_000)
        assert cost == 12.50
        
        # 1000 input + 500 output
        # input: 1000/1M * 2.50 = 0.0025
        # output: 500/1M * 10.00 = 0.005
        # total: 0.0075
        cost = config.calculate_cost(1000, 500)
        assert cost == pytest.approx(0.0075, rel=1e-6)
    
    def test_predefined_models_exist(self):
        """Test predefined models are available."""
        assert "gpt-4o" in MODELS
        assert "gpt-4o-mini" in MODELS
        assert "claude-3-5-sonnet" in MODELS
        assert "mock" in MODELS


class TestPromptBuilder:
    """Tests for PromptBuilder."""
    
    @pytest.fixture
    def builder(self):
        """Create prompt builder."""
        return PromptBuilder()
    
    def test_get_system_prompt_default(self, builder):
        """Test getting default system prompt."""
        prompt = builder.get_system_prompt("unknown_type")
        
        assert "Yadro" in prompt
        assert len(prompt) > 0
    
    def test_get_system_prompt_smm(self, builder):
        """Test getting SMM system prompt."""
        prompt = builder.get_system_prompt("smm")
        
        assert "social media" in prompt.lower()
    
    def test_build_prompt_analyze(self, builder):
        """Test building analyze template."""
        prompt = builder.build_prompt(
            "analyze",
            input_text="Test task"
        )
        
        assert "Test task" in prompt
        assert "analyze" in prompt.lower()
    
    def test_build_prompt_unknown(self, builder):
        """Test building unknown template returns input."""
        prompt = builder.build_prompt(
            "unknown_template",
            input_text="Just text"
        )
        
        assert prompt == "Just text"
    
    def test_add_custom_template(self, builder):
        """Test adding custom template."""
        builder.add_task_template(
            "custom",
            "Custom: {input_text}"
        )
        
        prompt = builder.build_prompt("custom", input_text="Hello")
        assert prompt == "Custom: Hello"


class TestModelRouter:
    """Tests for ModelRouter."""
    
    @pytest.fixture
    def router(self):
        """Create router."""
        return ModelRouter()
    
    def test_select_model_default(self, router):
        """Test selecting default model."""
        model = router.select_model()
        
        assert model is not None
        assert model.name is not None
    
    def test_select_model_task_override(self):
        """Test task-specific model override."""
        config = RouterConfig(
            task_model_overrides={"smm": "gpt-4o"}
        )
        router = ModelRouter(config)
        
        model = router.select_model(task_type="smm")
        
        assert model.name == "gpt-4o"
    
    def test_select_model_vision_required(self, router):
        """Test selecting model with vision."""
        model = router.select_model(requires_vision=True)
        
        assert model.supports_vision is True
    
    def test_get_fallback_chain(self, router):
        """Test getting fallback chain."""
        chain = router.get_fallback_chain("gpt-4o")
        
        assert "gpt-4o" in chain
        assert "mock" in chain  # Always last
        assert chain[-1] == "mock"
    
    def test_list_models(self, router):
        """Test listing models."""
        models = router.list_models()
        
        assert len(models) > 0
        assert "mock" in models
    
    def test_register_custom_model(self, router):
        """Test registering custom model."""
        custom = ModelConfig(
            name="custom-model",
            provider=LLMProvider.OPENAI,
        )
        
        router.register_model(custom)
        
        assert router.get_model("custom-model") is not None


class TestCostTracker:
    """Tests for CostTracker."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create database."""
        return Database(tmp_path / "test.sqlite3")
    
    @pytest.fixture
    def tracker(self, db):
        """Create cost tracker."""
        return CostTracker(db=db)
    
    @pytest.fixture
    def user_id(self, db):
        """Create test user."""
        return db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "testuser")
        )
    
    def test_record_usage(self, tracker, user_id):
        """Test recording usage."""
        response = LLMResponse(
            content="Test",
            model="gpt-4o-mini",
            provider=LLMProvider.OPENAI,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost_usd=0.001,
        )
        
        tracker.record(response, user_id=user_id)
        
        usage = tracker.get_user_usage(user_id)
        assert usage.total_input_tokens == 100
        assert usage.total_output_tokens == 50
        assert usage.call_count == 1
    
    def test_multiple_records(self, tracker, user_id):
        """Test multiple usage records."""
        for i in range(3):
            response = LLMResponse(
                content="Test",
                model="mock",
                provider=LLMProvider.MOCK,
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                cost_usd=0.01,
            )
            tracker.record(response, user_id=user_id)
        
        usage = tracker.get_user_usage(user_id)
        
        assert usage.call_count == 3
        assert usage.total_input_tokens == 300
        assert usage.total_cost_usd == pytest.approx(0.03)
    
    def test_check_budget(self, tracker, user_id):
        """Test budget checking."""
        response = LLMResponse(
            content="Test",
            model="mock",
            provider=LLMProvider.MOCK,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost_usd=0.50,
        )
        tracker.record(response, user_id=user_id)
        
        # Within budget
        assert tracker.check_budget(user_id, budget_usd=1.00) is True
        
        # Over budget
        assert tracker.check_budget(user_id, budget_usd=0.25) is False
    
    def test_get_remaining_budget(self, tracker, user_id):
        """Test remaining budget calculation."""
        response = LLMResponse(
            content="Test",
            model="mock",
            provider=LLMProvider.MOCK,
            cost_usd=0.30,
        )
        tracker.record(response, user_id=user_id)
        
        remaining = tracker.get_remaining_budget(user_id, budget_usd=1.00)
        
        assert remaining == pytest.approx(0.70)
    
    def test_task_usage(self, tracker, db, user_id):
        """Test task-specific usage tracking."""
        task_id = db.execute(
            "INSERT INTO tasks (user_id, status) VALUES (?, ?)",
            (user_id, "running")
        )
        
        response = LLMResponse(
            content="Test",
            model="mock",
            provider=LLMProvider.MOCK,
            input_tokens=200,
            output_tokens=100,
            total_tokens=300,
            cost_usd=0.05,
        )
        tracker.record(response, user_id=user_id, task_id=task_id)
        
        task_usage = tracker.get_task_usage(task_id)
        
        assert task_usage.total_tokens == 300
        assert task_usage.total_cost_usd == 0.05


class TestLLMService:
    """Tests for LLMService."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create database."""
        return Database(tmp_path / "test.sqlite3")
    
    @pytest.fixture
    def service(self, db):
        """Create LLM service in mock mode."""
        return LLMService(db=db, mock_mode=True)
    
    @pytest.fixture
    def user_id(self, db):
        """Create test user."""
        return db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "testuser")
        )
    
    def test_complete_basic(self, service):
        """Test basic completion."""
        messages = [
            Message.system("You are helpful"),
            Message.user("Hello"),
        ]
        
        response = service.complete(messages)
        
        assert response.content is not None
        assert len(response.content) > 0
        assert response.model is not None
    
    def test_complete_simple(self, service):
        """Test simple string completion."""
        content = service.complete_simple("What is Python?")
        
        assert isinstance(content, str)
        assert len(content) > 0
    
    def test_complete_template(self, service):
        """Test template-based completion."""
        response = service.complete_template(
            "analyze",
            input_text="Build a web app",
        )
        
        assert response.content is not None
        assert "analysis" in response.content.lower() or "analyze" in response.content.lower()
    
    def test_complete_tracks_cost(self, service, user_id):
        """Test completion tracks cost."""
        messages = [Message.user("Hello")]
        
        service.complete(messages, user_id=user_id)
        
        usage = service.cost_tracker.get_user_usage(user_id)
        assert usage.call_count == 1
    
    def test_mock_response_analysis(self, service):
        """Test mock generates analysis response."""
        messages = [Message.user("Analyze this data")]
        
        response = service.complete(messages)
        
        assert "analysis" in response.content.lower()
    
    def test_mock_response_draft(self, service):
        """Test mock generates draft response."""
        messages = [Message.user("Write a post about AI")]
        
        response = service.complete(messages)
        
        assert len(response.content) > 0
    
    def test_estimate_cost(self, service):
        """Test cost estimation."""
        messages = [
            Message.system("System prompt" * 100),
            Message.user("User message" * 50),
        ]
        
        cost = service.estimate_cost(messages, model="gpt-4o-mini")
        
        assert cost > 0
        assert cost < 1.0  # Should be reasonable
    
    def test_response_has_tokens(self, service):
        """Test response includes token counts."""
        messages = [Message.user("Hello world")]
        
        response = service.complete(messages)
        
        assert response.input_tokens > 0
        assert response.output_tokens > 0
        assert response.total_tokens == response.input_tokens + response.output_tokens
    
    def test_response_has_latency(self, service):
        """Test response includes latency."""
        messages = [Message.user("Quick test")]
        
        response = service.complete(messages)
        
        assert response.latency_ms is not None
        assert response.latency_ms >= 0


class TestUsageSummary:
    """Tests for UsageSummary."""
    
    def test_add_response(self):
        """Test adding response to summary."""
        summary = UsageSummary()
        
        response = LLMResponse(
            content="Test",
            model="mock",
            provider=LLMProvider.MOCK,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost_usd=0.01,
        )
        
        summary.add(response)
        
        assert summary.total_input_tokens == 100
        assert summary.total_output_tokens == 50
        assert summary.total_tokens == 150
        assert summary.total_cost_usd == 0.01
        assert summary.call_count == 1
    
    def test_add_multiple(self):
        """Test adding multiple responses."""
        summary = UsageSummary()
        
        for _ in range(5):
            response = LLMResponse(
                content="Test",
                model="mock",
                provider=LLMProvider.MOCK,
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                cost_usd=0.01,
            )
            summary.add(response)
        
        assert summary.call_count == 5
        assert summary.total_input_tokens == 500
        assert summary.total_cost_usd == pytest.approx(0.05)


class TestLLMRateLimiter:
    """Tests for LLMRateLimiter."""
    
    @pytest.fixture
    def limiter(self):
        """Create rate limiter."""
        return LLMRateLimiter()
    
    def test_record_and_count(self, limiter):
        """Test recording and counting requests."""
        limiter.record(user_id=1, tokens=100)
        limiter.record(user_id=1, tokens=200)
        limiter.record(user_id=1, tokens=150)
        
        count = limiter.get_requests_in_window(user_id=1, seconds=60)
        tokens = limiter.get_tokens_in_window(user_id=1, seconds=60)
        
        assert count == 3
        assert tokens == 450
    
    def test_separate_users(self, limiter):
        """Test users are tracked separately."""
        limiter.record(user_id=1, tokens=100)
        limiter.record(user_id=1, tokens=100)
        limiter.record(user_id=2, tokens=500)
        
        assert limiter.get_requests_in_window(1, 60) == 2
        assert limiter.get_requests_in_window(2, 60) == 1
        assert limiter.get_tokens_in_window(1, 60) == 200
        assert limiter.get_tokens_in_window(2, 60) == 500
    
    def test_clear_user(self, limiter):
        """Test clearing specific user."""
        limiter.record(user_id=1, tokens=100)
        limiter.record(user_id=2, tokens=200)
        
        limiter.clear(user_id=1)
        
        assert limiter.get_requests_in_window(1, 60) == 0
        assert limiter.get_requests_in_window(2, 60) == 1


class TestLLMServiceConfig:
    """Tests for LLMServiceConfig."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = LLMServiceConfig()
        
        assert config.max_input_tokens_per_request == 50000
        assert config.max_requests_per_minute == 10
        assert config.max_cost_per_day == 20.00
        assert config.emergency_stop is False
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = LLMServiceConfig(
            max_requests_per_minute=5,
            max_cost_per_hour=1.00,
            emergency_stop=True,
        )
        
        assert config.max_requests_per_minute == 5
        assert config.max_cost_per_hour == 1.00
        assert config.emergency_stop is True


class TestLLMServiceSecurity:
    """Tests for LLM Service security features."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create database."""
        return Database(tmp_path / "test.sqlite3")
    
    @pytest.fixture
    def user_id(self, db):
        """Create test user."""
        return db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "testuser")
        )
    
    def test_rate_limit_requests_per_minute(self, db, user_id):
        """Test rate limiting by requests per minute."""
        config = LLMServiceConfig(max_requests_per_minute=3)
        service = LLMService(db=db, config=config, mock_mode=True)
        
        messages = [Message.user("Test")]
        
        # First 3 should succeed
        for _ in range(3):
            service.complete(messages, user_id=user_id)
        
        # 4th should fail
        with pytest.raises(LLMRateLimitError) as exc_info:
            service.complete(messages, user_id=user_id)
        
        assert "requests/minute" in str(exc_info.value)
    
    def test_token_limit_per_request(self, db, user_id):
        """Test token limit per request."""
        config = LLMServiceConfig(max_input_tokens_per_request=100)
        service = LLMService(db=db, config=config, mock_mode=True)
        
        # Create message with ~200 tokens (800 chars)
        long_message = "x" * 800
        messages = [Message.user(long_message)]
        
        with pytest.raises(TokenLimitError) as exc_info:
            service.complete(messages, user_id=user_id)
        
        assert "Input too large" in str(exc_info.value)
    
    def test_budget_limit_per_hour(self, db, user_id):
        """Test budget limit per hour."""
        config = LLMServiceConfig(max_cost_per_hour=0.0000001)  # Extremely low limit
        service = LLMService(db=db, config=config, mock_mode=True)
        
        messages = [Message.user("Test " * 100)]  # Generate some cost
        
        # First call succeeds and uses budget
        service.complete(messages, user_id=user_id)
        
        # Second call should fail due to budget
        with pytest.raises(BudgetExceededError) as exc_info:
            service.complete(messages, user_id=user_id)
        
        assert "budget exceeded" in str(exc_info.value).lower()
    
    def test_emergency_stop(self, db, user_id):
        """Test emergency stop blocks all requests."""
        config = LLMServiceConfig(emergency_stop=True)
        service = LLMService(db=db, config=config, mock_mode=True)
        
        messages = [Message.user("Test")]
        
        with pytest.raises(Exception) as exc_info:
            service.complete(messages, user_id=user_id)
        
        assert "emergency stop" in str(exc_info.value).lower()
    
    def test_set_emergency_stop(self, db, user_id):
        """Test enabling emergency stop."""
        service = LLMService(db=db, mock_mode=True)
        messages = [Message.user("Test")]
        
        # Works normally
        service.complete(messages, user_id=user_id)
        
        # Enable emergency stop
        service.set_emergency_stop(True)
        
        # Now blocked
        with pytest.raises(Exception):
            service.complete(messages, user_id=user_id)
        
        # Disable emergency stop
        service.set_emergency_stop(False)
        
        # Works again
        service.complete(messages, user_id=user_id)
    
    def test_skip_limits_for_system_calls(self, db, user_id):
        """Test skip_limits parameter for internal calls."""
        config = LLMServiceConfig(max_requests_per_minute=1)
        service = LLMService(db=db, config=config, mock_mode=True)
        
        messages = [Message.user("Test")]
        
        # First call uses the limit
        service.complete(messages, user_id=user_id)
        
        # Second call with skip_limits should work
        response = service.complete(messages, user_id=user_id, skip_limits=True)
        assert response.content is not None
    
    def test_get_user_limits_status(self, db, user_id):
        """Test getting user limits status."""
        service = LLMService(db=db, mock_mode=True)
        messages = [Message.user("Test")]
        
        # Make some calls
        service.complete(messages, user_id=user_id)
        service.complete(messages, user_id=user_id)
        
        status = service.get_user_limits_status(user_id)
        
        assert status["requests_per_minute"]["used"] == 2
        assert status["requests_per_hour"]["used"] == 2
        assert "limit" in status["requests_per_minute"]
        assert "cost_per_hour" in status
