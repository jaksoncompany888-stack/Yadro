"""
Yadro v0 - Tool Runtime (Layer 4)
"""
from .models import ToolSpec, ToolResult, ToolCall, ToolImpact
from .registry import ToolRegistry, registry
from .policy import PolicyEngine, PolicyConfig, PolicyCheckResult, RateLimiter
from .runtime import (
    ToolRuntime,
    ToolExecutionError,
    ToolNotFoundError,
    PolicyViolationError,
    register_builtin_tools,
)
# Browser (опционально, нужен playwright)
try:
    from .browser import BrowserTool, SearchResult, web_search
    HAS_BROWSER = True
except ImportError:
    HAS_BROWSER = False
    BrowserTool = None
    SearchResult = None
    web_search = None

# Agent (опционально, нужен openai)
try:
    from .agent import BrowserAgent, run_agent
    HAS_AGENT = True
except ImportError:
    HAS_AGENT = False

__all__ = [
    "ToolSpec", "ToolResult", "ToolCall", "ToolImpact",
    "ToolRegistry", "registry",
    "PolicyEngine", "PolicyConfig", "PolicyCheckResult", "RateLimiter",
    "ToolRuntime", "ToolExecutionError", "ToolNotFoundError", "PolicyViolationError",
    "register_builtin_tools",
    "BrowserTool", "SearchResult", "web_search", "HAS_BROWSER",
    "BrowserAgent", "run_agent", "HAS_AGENT",
]
