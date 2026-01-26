"""
Yadro v0 - LLM Service (Layer 5)

LLM interactions with routing, prompts, and cost tracking.
"""
from .models import (
    LLMProvider, MessageRole, Message,
    LLMRequest, LLMResponse, ModelConfig, MODELS,
)
from .prompts import PromptBuilder, prompt_builder, SYSTEM_PROMPTS, TASK_TEMPLATES
from .router import ModelRouter, RouterConfig, router
from .cost_tracker import CostTracker, UsageSummary
from .service import (
    LLMService, LLMServiceConfig, LLMRateLimiter,
    LLMError, LLMTimeoutError, LLMRateLimitError,
    BudgetExceededError, TokenLimitError,
)

__all__ = [
    # Models
    "LLMProvider",
    "MessageRole",
    "Message",
    "LLMRequest",
    "LLMResponse",
    "ModelConfig",
    "MODELS",
    # Prompts
    "PromptBuilder",
    "prompt_builder",
    "SYSTEM_PROMPTS",
    "TASK_TEMPLATES",
    # Router
    "ModelRouter",
    "RouterConfig",
    "router",
    # Cost Tracker
    "CostTracker",
    "UsageSummary",
    # Service
    "LLMService",
    "LLMServiceConfig",
    "LLMRateLimiter",
    "LLMError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "BudgetExceededError",
    "TokenLimitError",
]
