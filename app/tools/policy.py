"""
Yadro v0 - Policy Engine

Enforces tool usage policies and limits.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Set
from collections import defaultdict

from .models import ToolSpec, ToolImpact


@dataclass
class PolicyConfig:
    """
    Policy configuration.
    """
    # Rate limits
    max_tool_calls_per_minute: int = 30
    max_tool_calls_per_hour: int = 500
    max_tasks_per_hour: int = 100
    
    # Tool-specific limits
    tool_limits: Dict[str, int] = field(default_factory=dict)  # per minute
    
    # Domain allowlist for web tools
    allowed_domains: Set[str] = field(default_factory=set)
    
    # Command whitelist for shell tool
    allowed_commands: Set[str] = field(default_factory=set)
    
    # Resource limits
    max_output_size_bytes: int = 10 * 1024 * 1024  # 10 MB
    max_execution_time_seconds: int = 300  # 5 minutes


class RateLimiter:
    """
    Simple in-memory rate limiter.
    
    Tracks calls per user per time window.
    """
    
    def __init__(self):
        """Initialize rate limiter."""
        # user_id -> list of timestamps
        self._calls: Dict[int, List[datetime]] = defaultdict(list)
        # user_id -> tool_name -> list of timestamps
        self._tool_calls: Dict[int, Dict[str, List[datetime]]] = defaultdict(lambda: defaultdict(list))
    
    def record_call(self, user_id: int, tool_name: Optional[str] = None) -> None:
        """Record a call."""
        now = datetime.now(timezone.utc)
        self._calls[user_id].append(now)
        
        if tool_name:
            self._tool_calls[user_id][tool_name].append(now)
    
    def get_calls_in_window(
        self,
        user_id: int,
        window_seconds: int,
        tool_name: Optional[str] = None,
    ) -> int:
        """
        Count calls in time window.
        
        Args:
            user_id: User ID
            window_seconds: Time window in seconds
            tool_name: Optional tool name filter
            
        Returns:
            Number of calls in window
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=window_seconds)
        
        if tool_name:
            calls = self._tool_calls[user_id].get(tool_name, [])
        else:
            calls = self._calls[user_id]
        
        # Count calls after cutoff
        count = sum(1 for ts in calls if ts > cutoff)
        
        # Clean up old entries
        self._cleanup(user_id, cutoff, tool_name)
        
        return count
    
    def _cleanup(
        self,
        user_id: int,
        cutoff: datetime,
        tool_name: Optional[str] = None,
    ) -> None:
        """Remove old entries."""
        if tool_name:
            if user_id in self._tool_calls and tool_name in self._tool_calls[user_id]:
                self._tool_calls[user_id][tool_name] = [
                    ts for ts in self._tool_calls[user_id][tool_name]
                    if ts > cutoff
                ]
        else:
            if user_id in self._calls:
                self._calls[user_id] = [
                    ts for ts in self._calls[user_id]
                    if ts > cutoff
                ]
    
    def clear(self, user_id: Optional[int] = None) -> None:
        """Clear rate limit data."""
        if user_id:
            self._calls.pop(user_id, None)
            self._tool_calls.pop(user_id, None)
        else:
            self._calls.clear()
            self._tool_calls.clear()


@dataclass
class PolicyCheckResult:
    """Result of policy check."""
    allowed: bool
    reason: Optional[str] = None
    
    @classmethod
    def allow(cls) -> "PolicyCheckResult":
        return cls(allowed=True)
    
    @classmethod
    def deny(cls, reason: str) -> "PolicyCheckResult":
        return cls(allowed=False, reason=reason)


class PolicyEngine:
    """
    Policy Engine - enforces tool usage policies.
    
    Checks:
        - Rate limits (per user, per tool)
        - Tool availability for task type
        - Domain allowlist
        - Command whitelist
        - Impact-based approval
    """
    
    def __init__(self, config: Optional[PolicyConfig] = None):
        """
        Initialize PolicyEngine.
        
        Args:
            config: Policy configuration
        """
        self.config = config or PolicyConfig()
        self._rate_limiter = RateLimiter()
    
    def check_tool_call(
        self,
        tool: ToolSpec,
        user_id: int,
        task_type: str,
        parameters: Dict,
    ) -> PolicyCheckResult:
        """
        Check if tool call is allowed.
        
        Args:
            tool: Tool to call
            user_id: User making the call
            task_type: Type of task
            parameters: Tool parameters
            
        Returns:
            PolicyCheckResult
        """
        # Check task type allowlist
        if tool.allowed_task_types and task_type not in tool.allowed_task_types:
            return PolicyCheckResult.deny(
                f"Tool '{tool.name}' not allowed for task type '{task_type}'"
            )
        
        # Check global rate limit
        calls_per_minute = self._rate_limiter.get_calls_in_window(user_id, 60)
        if calls_per_minute >= self.config.max_tool_calls_per_minute:
            return PolicyCheckResult.deny(
                f"Rate limit exceeded: {calls_per_minute}/{self.config.max_tool_calls_per_minute} calls/minute"
            )
        
        calls_per_hour = self._rate_limiter.get_calls_in_window(user_id, 3600)
        if calls_per_hour >= self.config.max_tool_calls_per_hour:
            return PolicyCheckResult.deny(
                f"Rate limit exceeded: {calls_per_hour}/{self.config.max_tool_calls_per_hour} calls/hour"
            )
        
        # Check tool-specific rate limit
        if tool.name in self.config.tool_limits:
            tool_calls = self._rate_limiter.get_calls_in_window(user_id, 60, tool.name)
            tool_limit = self.config.tool_limits[tool.name]
            if tool_calls >= tool_limit:
                return PolicyCheckResult.deny(
                    f"Tool rate limit exceeded: {tool_calls}/{tool_limit} calls/minute for '{tool.name}'"
                )
        
        # Check domain allowlist for web tools
        if tool.name in ("web_fetch", "web_search"):
            url = parameters.get("url") or parameters.get("query", "")
            if self.config.allowed_domains:
                domain_allowed = any(
                    domain in url for domain in self.config.allowed_domains
                )
                if not domain_allowed and "://" in url:
                    return PolicyCheckResult.deny(
                        f"Domain not in allowlist"
                    )
        
        # Check command whitelist for shell tool
        if tool.name == "shell":
            command = parameters.get("command", "")
            if self.config.allowed_commands:
                cmd_base = command.split()[0] if command else ""
                if cmd_base not in self.config.allowed_commands:
                    return PolicyCheckResult.deny(
                        f"Command '{cmd_base}' not in whitelist"
                    )
        
        return PolicyCheckResult.allow()
    
    def record_call(self, user_id: int, tool_name: str) -> None:
        """Record a tool call for rate limiting."""
        self._rate_limiter.record_call(user_id, tool_name)
    
    def check_approval_required(self, tool: ToolSpec) -> bool:
        """Check if tool requires approval."""
        return tool.requires_approval or tool.impact == ToolImpact.HIGH
    
    def get_rate_limit_status(self, user_id: int) -> Dict:
        """Get current rate limit status for user."""
        return {
            "calls_per_minute": self._rate_limiter.get_calls_in_window(user_id, 60),
            "calls_per_hour": self._rate_limiter.get_calls_in_window(user_id, 3600),
            "limits": {
                "per_minute": self.config.max_tool_calls_per_minute,
                "per_hour": self.config.max_tool_calls_per_hour,
            },
        }
    
    def reset_rate_limits(self, user_id: Optional[int] = None) -> None:
        """Reset rate limits."""
        self._rate_limiter.clear(user_id)
