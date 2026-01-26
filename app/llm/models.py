"""
Yadro v0 - LLM Service Models

Data classes for LLM requests and responses.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any, Dict, List
from enum import Enum


class LLMProvider(str, Enum):
    """Supported LLM providers."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    MOCK = "mock"


class MessageRole(str, Enum):
    """Message roles."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class Message:
    """Chat message."""
    role: MessageRole
    content: str
    
    def to_dict(self) -> Dict[str, str]:
        return {
            "role": self.role.value,
            "content": self.content,
        }
    
    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role=MessageRole.SYSTEM, content=content)
    
    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role=MessageRole.USER, content=content)
    
    @classmethod
    def assistant(cls, content: str) -> "Message":
        return cls(role=MessageRole.ASSISTANT, content=content)


@dataclass
class LLMRequest:
    """Request to LLM."""
    messages: List[Message]
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 1000
    
    # Optional structured output
    json_schema: Optional[Dict] = None
    
    # Context
    task_id: Optional[int] = None
    user_id: Optional[int] = None
    purpose: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "messages": [m.to_dict() for m in self.messages],
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


@dataclass
class LLMResponse:
    """Response from LLM."""
    content: str
    model: str
    provider: LLMProvider
    
    # Token usage
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    
    # Cost
    cost_usd: float = 0.0
    
    # Metadata
    finish_reason: Optional[str] = None
    latency_ms: Optional[int] = None
    
    def to_dict(self) -> Dict:
        return {
            "content": self.content,
            "model": self.model,
            "provider": self.provider.value,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "finish_reason": self.finish_reason,
            "latency_ms": self.latency_ms,
        }


@dataclass
class ModelConfig:
    """Configuration for a model."""
    name: str
    provider: LLMProvider
    
    # Pricing per 1M tokens
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0
    
    # Limits
    max_context_tokens: int = 128000
    max_output_tokens: int = 4096
    
    # Capabilities
    supports_json: bool = True
    supports_vision: bool = False
    
    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost for token usage."""
        input_cost = (input_tokens / 1_000_000) * self.input_price_per_million
        output_cost = (output_tokens / 1_000_000) * self.output_price_per_million
        return input_cost + output_cost


# Pre-defined model configs
MODELS = {
    # OpenAI
    "gpt-4o": ModelConfig(
        name="gpt-4o",
        provider=LLMProvider.OPENAI,
        input_price_per_million=2.50,
        output_price_per_million=10.00,
        max_context_tokens=128000,
        supports_vision=True,
    ),
    "gpt-4o-mini": ModelConfig(
        name="gpt-4o-mini",
        provider=LLMProvider.OPENAI,
        input_price_per_million=0.15,
        output_price_per_million=0.60,
        max_context_tokens=128000,
    ),
    # Anthropic
    "claude-3-5-sonnet": ModelConfig(
        name="claude-3-5-sonnet-20241022",
        provider=LLMProvider.ANTHROPIC,
        input_price_per_million=3.00,
        output_price_per_million=15.00,
        max_context_tokens=200000,
        supports_vision=True,
    ),
    "claude-3-5-haiku": ModelConfig(
        name="claude-3-5-haiku-20241022",
        provider=LLMProvider.ANTHROPIC,
        input_price_per_million=0.80,
        output_price_per_million=4.00,
        max_context_tokens=200000,
    ),
    # Mock
    "mock": ModelConfig(
        name="mock",
        provider=LLMProvider.MOCK,
        input_price_per_million=0.0,
        output_price_per_million=0.0,
    ),
}
