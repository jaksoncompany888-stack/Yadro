"""
Tests for Layer 4: Tool Runtime

Run with: pytest -q
"""
import pytest
import time
from datetime import datetime, timezone

from app.storage import Database
from app.tools import (
    ToolSpec, ToolResult, ToolCall, ToolImpact,
    ToolRegistry, PolicyEngine, PolicyConfig, RateLimiter,
    ToolRuntime, ToolNotFoundError, PolicyViolationError,
    register_builtin_tools,
)


class TestToolRegistry:
    """Tests for ToolRegistry."""
    
    @pytest.fixture
    def registry(self):
        """Create fresh registry."""
        return ToolRegistry()
    
    def test_register_tool(self, registry):
        """Test registering a tool."""
        def dummy_handler(**kwargs):
            return {"result": "ok"}
        
        spec = registry.register(
            name="test_tool",
            handler=dummy_handler,
            description="Test tool",
            impact=ToolImpact.LOW,
        )
        
        assert spec.name == "test_tool"
        assert spec.description == "Test tool"
        assert spec.impact == ToolImpact.LOW
    
    def test_get_tool(self, registry):
        """Test getting registered tool."""
        def handler(**kwargs):
            return {}
        
        registry.register("my_tool", handler)
        
        tool = registry.get("my_tool")
        assert tool is not None
        assert tool.name == "my_tool"
    
    def test_get_nonexistent_tool(self, registry):
        """Test getting non-existent tool returns None."""
        tool = registry.get("nonexistent")
        assert tool is None
    
    def test_exists(self, registry):
        """Test checking tool existence."""
        def handler(**kwargs):
            return {}
        
        registry.register("exists_tool", handler)
        
        assert registry.exists("exists_tool") is True
        assert registry.exists("nonexistent") is False
    
    def test_list_tools(self, registry):
        """Test listing all tools."""
        def handler(**kwargs):
            return {}
        
        registry.register("tool1", handler)
        registry.register("tool2", handler)
        registry.register("tool3", handler)
        
        tools = registry.list()
        assert len(tools) == 3
        
        names = registry.list_names()
        assert set(names) == {"tool1", "tool2", "tool3"}
    
    def test_unregister_tool(self, registry):
        """Test unregistering a tool."""
        def handler(**kwargs):
            return {}
        
        registry.register("removable", handler)
        assert registry.exists("removable") is True
        
        removed = registry.unregister("removable")
        assert removed is True
        assert registry.exists("removable") is False
    
    def test_unregister_nonexistent(self, registry):
        """Test unregistering non-existent tool."""
        removed = registry.unregister("nonexistent")
        assert removed is False
    
    def test_list_for_task_type(self, registry):
        """Test filtering tools by task type."""
        def handler(**kwargs):
            return {}
        
        registry.register("general_tool", handler)
        registry.register(
            "smm_tool", handler,
            allowed_task_types=["smm"],
        )
        registry.register(
            "research_tool", handler,
            allowed_task_types=["research"],
        )
        
        smm_tools = registry.list_for_task_type("smm")
        assert len(smm_tools) == 2  # general_tool (no restriction) + smm_tool
        
        research_tools = registry.list_for_task_type("research")
        assert len(research_tools) == 2  # general_tool + research_tool
    
    def test_clear_registry(self, registry):
        """Test clearing registry."""
        def handler(**kwargs):
            return {}
        
        registry.register("tool1", handler)
        registry.register("tool2", handler)
        
        registry.clear()
        
        assert len(registry.list()) == 0


class TestRateLimiter:
    """Tests for RateLimiter."""
    
    @pytest.fixture
    def limiter(self):
        """Create fresh rate limiter."""
        return RateLimiter()
    
    def test_record_and_count_calls(self, limiter):
        """Test recording and counting calls."""
        limiter.record_call(user_id=1)
        limiter.record_call(user_id=1)
        limiter.record_call(user_id=1)
        
        count = limiter.get_calls_in_window(user_id=1, window_seconds=60)
        assert count == 3
    
    def test_separate_users(self, limiter):
        """Test calls are separated by user."""
        limiter.record_call(user_id=1)
        limiter.record_call(user_id=1)
        limiter.record_call(user_id=2)
        
        assert limiter.get_calls_in_window(1, 60) == 2
        assert limiter.get_calls_in_window(2, 60) == 1
    
    def test_tool_specific_counting(self, limiter):
        """Test tool-specific call counting."""
        limiter.record_call(user_id=1, tool_name="tool_a")
        limiter.record_call(user_id=1, tool_name="tool_a")
        limiter.record_call(user_id=1, tool_name="tool_b")
        
        assert limiter.get_calls_in_window(1, 60, tool_name="tool_a") == 2
        assert limiter.get_calls_in_window(1, 60, tool_name="tool_b") == 1
        assert limiter.get_calls_in_window(1, 60) == 3  # All calls
    
    def test_clear_user(self, limiter):
        """Test clearing specific user."""
        limiter.record_call(user_id=1)
        limiter.record_call(user_id=2)
        
        limiter.clear(user_id=1)
        
        assert limiter.get_calls_in_window(1, 60) == 0
        assert limiter.get_calls_in_window(2, 60) == 1
    
    def test_clear_all(self, limiter):
        """Test clearing all users."""
        limiter.record_call(user_id=1)
        limiter.record_call(user_id=2)
        
        limiter.clear()
        
        assert limiter.get_calls_in_window(1, 60) == 0
        assert limiter.get_calls_in_window(2, 60) == 0


class TestPolicyEngine:
    """Tests for PolicyEngine."""
    
    @pytest.fixture
    def policy(self):
        """Create policy engine with test config."""
        config = PolicyConfig(
            max_tool_calls_per_minute=5,
            max_tool_calls_per_hour=100,
            tool_limits={"limited_tool": 2},
            allowed_domains={"example.com", "test.org"},
            allowed_commands={"ls", "cat", "echo"},
        )
        return PolicyEngine(config)
    
    @pytest.fixture
    def basic_tool(self):
        """Create basic tool spec."""
        return ToolSpec(
            name="basic",
            description="Basic tool",
            handler=lambda **kwargs: {},
        )
    
    def test_allow_normal_call(self, policy, basic_tool):
        """Test allowing normal tool call."""
        result = policy.check_tool_call(
            tool=basic_tool,
            user_id=1,
            task_type="general",
            parameters={},
        )
        
        assert result.allowed is True
    
    def test_deny_rate_limit_exceeded(self, policy, basic_tool):
        """Test denying when rate limit exceeded."""
        # Record 5 calls (at limit)
        for _ in range(5):
            policy.record_call(user_id=1, tool_name="basic")
        
        result = policy.check_tool_call(
            tool=basic_tool,
            user_id=1,
            task_type="general",
            parameters={},
        )
        
        assert result.allowed is False
        assert "Rate limit" in result.reason
    
    def test_deny_tool_specific_limit(self, policy):
        """Test tool-specific rate limit."""
        tool = ToolSpec(
            name="limited_tool",
            description="Limited",
            handler=lambda **kwargs: {},
        )
        
        # Record 2 calls (at tool limit)
        policy.record_call(user_id=1, tool_name="limited_tool")
        policy.record_call(user_id=1, tool_name="limited_tool")
        
        result = policy.check_tool_call(
            tool=tool,
            user_id=1,
            task_type="general",
            parameters={},
        )
        
        assert result.allowed is False
        assert "limited_tool" in result.reason
    
    def test_deny_task_type_not_allowed(self, policy):
        """Test denying tool for wrong task type."""
        tool = ToolSpec(
            name="smm_only",
            description="SMM only tool",
            handler=lambda **kwargs: {},
            allowed_task_types=["smm"],
        )
        
        result = policy.check_tool_call(
            tool=tool,
            user_id=1,
            task_type="research",  # Wrong type
            parameters={},
        )
        
        assert result.allowed is False
        assert "not allowed for task type" in result.reason
    
    def test_deny_domain_not_allowed(self, policy):
        """Test denying URL with non-allowed domain."""
        tool = ToolSpec(
            name="web_fetch",
            description="Fetch",
            handler=lambda **kwargs: {},
        )
        
        result = policy.check_tool_call(
            tool=tool,
            user_id=1,
            task_type="general",
            parameters={"url": "https://blocked.com/page"},
        )
        
        assert result.allowed is False
        assert "allowlist" in result.reason
    
    def test_allow_domain_in_allowlist(self, policy):
        """Test allowing URL with allowed domain."""
        tool = ToolSpec(
            name="web_fetch",
            description="Fetch",
            handler=lambda **kwargs: {},
        )
        
        result = policy.check_tool_call(
            tool=tool,
            user_id=1,
            task_type="general",
            parameters={"url": "https://example.com/page"},
        )
        
        assert result.allowed is True
    
    def test_deny_command_not_whitelisted(self, policy):
        """Test denying non-whitelisted shell command."""
        tool = ToolSpec(
            name="shell",
            description="Shell",
            handler=lambda **kwargs: {},
        )
        
        result = policy.check_tool_call(
            tool=tool,
            user_id=1,
            task_type="general",
            parameters={"command": "rm -rf /"},
        )
        
        assert result.allowed is False
        assert "whitelist" in result.reason
    
    def test_allow_whitelisted_command(self, policy):
        """Test allowing whitelisted shell command."""
        tool = ToolSpec(
            name="shell",
            description="Shell",
            handler=lambda **kwargs: {},
        )
        
        result = policy.check_tool_call(
            tool=tool,
            user_id=1,
            task_type="general",
            parameters={"command": "ls -la"},
        )
        
        assert result.allowed is True
    
    def test_check_approval_required(self, policy):
        """Test checking approval requirement."""
        low_impact = ToolSpec(
            name="low",
            description="Low impact",
            handler=lambda **kwargs: {},
            impact=ToolImpact.LOW,
        )
        
        high_impact = ToolSpec(
            name="high",
            description="High impact",
            handler=lambda **kwargs: {},
            impact=ToolImpact.HIGH,
        )
        
        explicit_approval = ToolSpec(
            name="explicit",
            description="Explicit approval",
            handler=lambda **kwargs: {},
            requires_approval=True,
        )
        
        assert policy.check_approval_required(low_impact) is False
        assert policy.check_approval_required(high_impact) is True
        assert policy.check_approval_required(explicit_approval) is True
    
    def test_get_rate_limit_status(self, policy):
        """Test getting rate limit status."""
        policy.record_call(user_id=1, tool_name="test")
        policy.record_call(user_id=1, tool_name="test")
        
        status = policy.get_rate_limit_status(user_id=1)
        
        assert status["calls_per_minute"] == 2
        assert status["limits"]["per_minute"] == 5


class TestToolRuntime:
    """Tests for ToolRuntime."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create database."""
        return Database(tmp_path / "test.sqlite3")
    
    @pytest.fixture
    def runtime(self, db):
        """Create runtime with built-in tools."""
        rt = ToolRuntime(db=db)
        register_builtin_tools(rt)
        return rt
    
    @pytest.fixture
    def user_id(self, db):
        """Create test user."""
        return db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "testuser")
        )
    
    def test_execute_tool(self, runtime, user_id):
        """Test executing a tool."""
        result = runtime.execute(
            tool_name="web_search",
            parameters={"query": "test"},
            user_id=user_id,
        )
        
        assert result.success is True
        assert result.data is not None
        assert "results" in result.data
    
    def test_execute_nonexistent_tool(self, runtime, user_id):
        """Test executing non-existent tool raises error."""
        with pytest.raises(ToolNotFoundError):
            runtime.execute(
                tool_name="nonexistent",
                parameters={},
                user_id=user_id,
            )
    
    def test_execute_policy_violation(self, runtime, user_id):
        """Test policy violation raises error."""
        # telegram_publish only allowed for smm task type
        with pytest.raises(PolicyViolationError):
            runtime.execute(
                tool_name="telegram_publish",
                parameters={"channel": "@test", "content": "Hello"},
                user_id=user_id,
                task_type="research",  # Wrong type
            )
    
    def test_execute_logs_to_database(self, runtime, db, user_id):
        """Test tool execution is logged."""
        task_id = db.execute(
            "INSERT INTO tasks (user_id, status) VALUES (?, ?)",
            (user_id, "running")
        )
        
        runtime.execute(
            tool_name="web_search",
            parameters={"query": "test"},
            user_id=user_id,
            task_id=task_id,
        )
        
        events = db.fetch_all(
            "SELECT * FROM task_events WHERE task_id = ? AND event_type = ?",
            (task_id, "tool_called")
        )
        
        assert len(events) == 1
        assert events[0]["tool_name"] == "web_search"
    
    def test_execute_with_timeout(self, db, user_id):
        """Test tool timeout handling."""
        def slow_handler(**kwargs):
            time.sleep(2)
            return {"done": True}
        
        runtime = ToolRuntime(db=db)
        runtime.registry.register(
            name="slow_tool",
            handler=slow_handler,
            timeout_seconds=1,  # 1 second timeout
        )
        
        result = runtime.execute(
            tool_name="slow_tool",
            parameters={},
            user_id=user_id,
        )
        
        assert result.success is False
        assert "timed out" in result.error
    
    def test_check_approval_required(self, runtime):
        """Test checking approval requirement."""
        assert runtime.check_approval_required("telegram_publish") is True
        assert runtime.check_approval_required("web_search") is False


class TestToolModels:
    """Tests for tool models."""
    
    def test_tool_spec_to_dict(self):
        """Test ToolSpec.to_dict excludes handler."""
        spec = ToolSpec(
            name="test",
            description="Test tool",
            handler=lambda **kwargs: {},
            impact=ToolImpact.MEDIUM,
        )
        
        data = spec.to_dict()
        
        assert data["name"] == "test"
        assert data["impact"] == "medium"
        assert "handler" not in data
    
    def test_tool_result_to_dict(self):
        """Test ToolResult.to_dict."""
        result = ToolResult(
            success=True,
            data={"key": "value"},
            tool_name="test",
            execution_time_ms=100,
        )
        
        data = result.to_dict()
        
        assert data["success"] is True
        assert data["data"] == {"key": "value"}
        assert data["execution_time_ms"] == 100
    
    def test_tool_call_execution_time(self):
        """Test ToolCall.execution_time_ms calculation."""
        now = datetime.now(timezone.utc)
        
        call = ToolCall(
            tool_name="test",
            parameters={},
            called_at=now,
            completed_at=now,
        )
        
        # Same time = 0ms
        assert call.execution_time_ms == 0


class TestBuiltinTools:
    """Tests for built-in tools."""
    
    @pytest.fixture
    def runtime(self, tmp_path):
        """Create runtime with built-in tools."""
        db = Database(tmp_path / "test.sqlite3")
        rt = ToolRuntime(db=db)
        register_builtin_tools(rt)
        
        # Create user
        db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "testuser")
        )
        return rt
    
    def test_builtin_tools_registered(self, runtime):
        """Test all built-in tools are registered."""
        names = runtime.registry.list_names()
        
        assert "web_search" in names
        assert "web_fetch" in names
        assert "file_read" in names
        assert "file_write" in names
        assert "shell" in names
        assert "telegram_publish" in names
    
    def test_web_search_returns_results(self, runtime):
        """Test web_search returns results."""
        result = runtime.execute(
            tool_name="web_search",
            parameters={"query": "python", "limit": 5},
            user_id=1,
        )
        
        assert result.success is True
        assert "results" in result.data
        assert len(result.data["results"]) <= 5
    
    def test_web_fetch_returns_content(self, runtime):
        """Test web_fetch returns content."""
        result = runtime.execute(
            tool_name="web_fetch",
            parameters={"url": "https://example.com"},
            user_id=1,
        )
        
        assert result.success is True
        assert "content" in result.data
    
    def test_telegram_publish_requires_smm(self, runtime):
        """Test telegram_publish requires smm task type."""
        # Should fail for general
        with pytest.raises(PolicyViolationError):
            runtime.execute(
                tool_name="telegram_publish",
                parameters={"channel": "@test", "content": "Hello"},
                user_id=1,
                task_type="general",
            )
        
        # Should work for smm
        result = runtime.execute(
            tool_name="telegram_publish",
            parameters={"channel": "@test", "content": "Hello"},
            user_id=1,
            task_type="smm",
        )
        
        assert result.success is True
