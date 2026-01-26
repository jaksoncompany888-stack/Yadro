"""
Yadro v0 - LLM Service

Main service for LLM interactions.
"""
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from .models import (
    LLMRequest, LLMResponse, Message, MessageRole,
    LLMProvider, ModelConfig, MODELS,
)
from .prompts import PromptBuilder, prompt_builder
from .router import ModelRouter, router
from .cost_tracker import CostTracker
from ..storage import Database


class LLMError(Exception):
    """Base LLM error."""
    pass


class LLMTimeoutError(LLMError):
    """LLM request timed out."""
    pass


class LLMRateLimitError(LLMError):
    """LLM rate limit exceeded."""
    pass


class BudgetExceededError(LLMError):
    """Budget limit exceeded."""
    pass


class TokenLimitError(LLMError):
    """Token limit exceeded."""
    pass


class LLMServiceConfig:
    """Configuration for LLM Service security limits."""
    
    def __init__(
        self,
        # Per-request limits
        max_input_tokens_per_request: int = 50000,
        max_output_tokens_per_request: int = 4000,
        
        # Per-user rate limits
        max_requests_per_minute: int = 10,
        max_requests_per_hour: int = 100,
        max_tokens_per_hour: int = 500000,
        
        # Per-user budget limits
        max_cost_per_request: float = 0.50,
        max_cost_per_hour: float = 5.00,
        max_cost_per_day: float = 20.00,
        
        # Global system limits
        global_max_cost_per_hour: float = 100.00,
        global_max_cost_per_day: float = 500.00,
        
        # Emergency stop
        emergency_stop: bool = False,
    ):
        self.max_input_tokens_per_request = max_input_tokens_per_request
        self.max_output_tokens_per_request = max_output_tokens_per_request
        self.max_requests_per_minute = max_requests_per_minute
        self.max_requests_per_hour = max_requests_per_hour
        self.max_tokens_per_hour = max_tokens_per_hour
        self.max_cost_per_request = max_cost_per_request
        self.max_cost_per_hour = max_cost_per_hour
        self.max_cost_per_day = max_cost_per_day
        self.global_max_cost_per_hour = global_max_cost_per_hour
        self.global_max_cost_per_day = global_max_cost_per_day
        self.emergency_stop = emergency_stop


class LLMRateLimiter:
    """Rate limiter for LLM requests."""
    
    def __init__(self):
        # user_id -> list of (timestamp, tokens)
        self._requests: Dict[int, List[tuple]] = {}
    
    def record(self, user_id: int, tokens: int) -> None:
        """Record a request."""
        now = datetime.now(timezone.utc)
        if user_id not in self._requests:
            self._requests[user_id] = []
        self._requests[user_id].append((now, tokens))
        self._cleanup(user_id)
    
    def get_requests_in_window(self, user_id: int, seconds: int) -> int:
        """Count requests in time window."""
        self._cleanup(user_id)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        requests = self._requests.get(user_id, [])
        return sum(1 for ts, _ in requests if ts > cutoff)
    
    def get_tokens_in_window(self, user_id: int, seconds: int) -> int:
        """Count tokens in time window."""
        self._cleanup(user_id)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        requests = self._requests.get(user_id, [])
        return sum(tokens for ts, tokens in requests if ts > cutoff)
    
    def _cleanup(self, user_id: int) -> None:
        """Remove old entries (older than 24h)."""
        if user_id not in self._requests:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        self._requests[user_id] = [
            (ts, tokens) for ts, tokens in self._requests[user_id]
            if ts > cutoff
        ]
    
    def clear(self, user_id: Optional[int] = None) -> None:
        """Clear rate limit data."""
        if user_id:
            self._requests.pop(user_id, None)
        else:
            self._requests.clear()


class LLMService:
    """
    LLM Service - handles all LLM interactions.
    
    Features:
    - Model routing
    - Prompt building
    - Cost tracking
    - Retry with fallback
    - Mock mode for testing
    - **Rate limiting**
    - **Budget limits**
    - **Token limits**
    """
    
    DEFAULT_TIMEOUT = 30  # seconds
    MAX_RETRIES = 2
    
    def __init__(
        self,
        db: Optional[Database] = None,
        router: Optional[ModelRouter] = None,
        prompt_builder: Optional[PromptBuilder] = None,
        cost_tracker: Optional[CostTracker] = None,
        config: Optional[LLMServiceConfig] = None,
        mock_mode: bool = True,  # Default to mock for MVP
        openai_api_key: Optional[str] = None,
    ):
        """
        Initialize LLM Service.
        
        Args:
            db: Database for logging
            router: Model router
            prompt_builder: Prompt builder
            cost_tracker: Cost tracker
            config: Security configuration
            mock_mode: Use mock responses
            openai_api_key: OpenAI API key
        """
        self._db = db
        self._router = router or ModelRouter()
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._cost_tracker = cost_tracker
        self._config = config or LLMServiceConfig()
        self._mock_mode = mock_mode
        self._openai_api_key = openai_api_key
        self._rate_limiter = LLMRateLimiter()
    
    @property
    def db(self) -> Database:
        """Get database (lazy init)."""
        if self._db is None:
            self._db = Database()
        return self._db
    
    @property
    def cost_tracker(self) -> CostTracker:
        """Get cost tracker (lazy init)."""
        if self._cost_tracker is None:
            self._cost_tracker = CostTracker(db=self.db)
        return self._cost_tracker
    
    @property
    def config(self) -> LLMServiceConfig:
        """Get configuration."""
        return self._config
    
    def complete(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        task_type: str = "general",
        temperature: float = 0.7,
        max_tokens: int = 1000,
        user_id: Optional[int] = None,
        task_id: Optional[int] = None,
        timeout: Optional[int] = None,
        skip_limits: bool = False,  # For internal/system calls
    ) -> LLMResponse:
        """
        Get completion from LLM.
        
        Args:
            messages: Chat messages
            model: Specific model (or auto-select)
            task_type: Task type for routing
            temperature: Sampling temperature
            max_tokens: Max output tokens
            user_id: User ID for tracking
            task_id: Task ID for tracking
            timeout: Request timeout
            skip_limits: Skip rate/budget limits (internal use)
            
        Returns:
            LLMResponse
            
        Raises:
            BudgetExceededError: If budget limit exceeded
            LLMRateLimitError: If rate limit exceeded
            TokenLimitError: If token limit exceeded
        """
        # Emergency stop check
        if self._config.emergency_stop:
            raise LLMError("LLM Service is in emergency stop mode")
        
        timeout = timeout or self.DEFAULT_TIMEOUT
        
        # Check limits before making request
        if user_id and not skip_limits:
            self._check_limits(user_id, messages, max_tokens)
        
        # Select model if not specified
        if model is None:
            model_config = self._router.select_model(task_type=task_type)
            model = model_config.name
        else:
            model_config = self._router.get_model(model) or MODELS.get("mock")
        
        # Enforce max_tokens limit
        max_tokens = min(max_tokens, self._config.max_output_tokens_per_request)
        
        # Create request
        request = LLMRequest(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            task_id=task_id,
            user_id=user_id,
        )
        
        # Execute with retry
        start_time = time.time()
        response = self._execute_with_retry(request, model_config, timeout)
        response.latency_ms = int((time.time() - start_time) * 1000)
        
        # Check cost limit
        if user_id and not skip_limits:
            if response.cost_usd > self._config.max_cost_per_request:
                # Log but don't fail - already executed
                pass
        
        # Track cost and rate limit
        if user_id:
            self.cost_tracker.record(
                response=response,
                user_id=user_id,
                task_id=task_id,
            )
            self._rate_limiter.record(user_id, response.total_tokens)
        
        return response
    
    def _check_limits(
        self,
        user_id: int,
        messages: List[Message],
        max_tokens: int,
    ) -> None:
        """
        Check all limits before making request.
        
        Raises:
            BudgetExceededError: If budget exceeded
            LLMRateLimitError: If rate limit exceeded
            TokenLimitError: If token limit exceeded
        """
        # Estimate input tokens
        input_tokens = sum(len(m.content) // 4 for m in messages)
        
        # Check input token limit
        if input_tokens > self._config.max_input_tokens_per_request:
            raise TokenLimitError(
                f"Input too large: ~{input_tokens} tokens "
                f"(max {self._config.max_input_tokens_per_request})"
            )
        
        # Check rate limits
        requests_per_minute = self._rate_limiter.get_requests_in_window(user_id, 60)
        if requests_per_minute >= self._config.max_requests_per_minute:
            raise LLMRateLimitError(
                f"Rate limit: {requests_per_minute}/{self._config.max_requests_per_minute} requests/minute"
            )
        
        requests_per_hour = self._rate_limiter.get_requests_in_window(user_id, 3600)
        if requests_per_hour >= self._config.max_requests_per_hour:
            raise LLMRateLimitError(
                f"Rate limit: {requests_per_hour}/{self._config.max_requests_per_hour} requests/hour"
            )
        
        # Check tokens per hour
        tokens_per_hour = self._rate_limiter.get_tokens_in_window(user_id, 3600)
        if tokens_per_hour >= self._config.max_tokens_per_hour:
            raise LLMRateLimitError(
                f"Token limit: {tokens_per_hour}/{self._config.max_tokens_per_hour} tokens/hour"
            )
        
        # Check budget limits
        now = datetime.now(timezone.utc)
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)
        
        cost_per_hour = self.cost_tracker.get_user_usage(user_id, hour_ago).total_cost_usd
        if cost_per_hour >= self._config.max_cost_per_hour:
            raise BudgetExceededError(
                f"Hourly budget exceeded: ${cost_per_hour:.2f}/${self._config.max_cost_per_hour:.2f}"
            )
        
        cost_per_day = self.cost_tracker.get_user_usage(user_id, day_ago).total_cost_usd
        if cost_per_day >= self._config.max_cost_per_day:
            raise BudgetExceededError(
                f"Daily budget exceeded: ${cost_per_day:.2f}/${self._config.max_cost_per_day:.2f}"
            )
        
        # Check global limits
        self._check_global_limits()
    
    def _check_global_limits(self) -> None:
        """Check global system limits."""
        now = datetime.now(timezone.utc)
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)
        
        # Get global usage from database
        row = self.db.fetch_one(
            """SELECT COALESCE(SUM(cost_usd), 0) as cost
               FROM costs WHERE created_at >= ?""",
            (hour_ago.isoformat(),)
        )
        global_cost_hour = row["cost"] if row else 0
        
        if global_cost_hour >= self._config.global_max_cost_per_hour:
            raise BudgetExceededError(
                f"Global hourly limit exceeded: ${global_cost_hour:.2f}"
            )
        
        row = self.db.fetch_one(
            """SELECT COALESCE(SUM(cost_usd), 0) as cost
               FROM costs WHERE created_at >= ?""",
            (day_ago.isoformat(),)
        )
        global_cost_day = row["cost"] if row else 0
        
        if global_cost_day >= self._config.global_max_cost_per_day:
            raise BudgetExceededError(
                f"Global daily limit exceeded: ${global_cost_day:.2f}"
            )
    
    def set_emergency_stop(self, enabled: bool) -> None:
        """Enable/disable emergency stop."""
        self._config.emergency_stop = enabled
    
    def get_user_limits_status(self, user_id: int) -> Dict:
        """Get current limits status for user."""
        now = datetime.now(timezone.utc)
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)
        
        return {
            "requests_per_minute": {
                "used": self._rate_limiter.get_requests_in_window(user_id, 60),
                "limit": self._config.max_requests_per_minute,
            },
            "requests_per_hour": {
                "used": self._rate_limiter.get_requests_in_window(user_id, 3600),
                "limit": self._config.max_requests_per_hour,
            },
            "tokens_per_hour": {
                "used": self._rate_limiter.get_tokens_in_window(user_id, 3600),
                "limit": self._config.max_tokens_per_hour,
            },
            "cost_per_hour": {
                "used": self.cost_tracker.get_user_usage(user_id, hour_ago).total_cost_usd,
                "limit": self._config.max_cost_per_hour,
            },
            "cost_per_day": {
                "used": self.cost_tracker.get_user_usage(user_id, day_ago).total_cost_usd,
                "limit": self._config.max_cost_per_day,
            },
        }
    
    def complete_simple(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        task_type: str = "general",
        **kwargs,
    ) -> str:
        """
        Simple completion with string prompt.
        
        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            task_type: Task type
            **kwargs: Additional args for complete()
            
        Returns:
            Response content string
        """
        messages = []
        
        # Add system prompt
        if system_prompt is None:
            system_prompt = self._prompt_builder.get_system_prompt(task_type)
        messages.append(Message.system(system_prompt))
        
        # Add user message
        messages.append(Message.user(prompt))
        
        response = self.complete(messages, task_type=task_type, **kwargs)
        return response.content
    
    def complete_template(
        self,
        template_name: str,
        task_type: str = "general",
        user_id: Optional[int] = None,
        task_id: Optional[int] = None,
        **template_vars,
    ) -> LLMResponse:
        """
        Complete using a prompt template.
        
        Args:
            template_name: Template name
            task_type: Task type
            user_id: User ID
            task_id: Task ID
            **template_vars: Template variables
            
        Returns:
            LLMResponse
        """
        # Build prompt from template
        prompt = self._prompt_builder.build_prompt(template_name, **template_vars)
        system_prompt = self._prompt_builder.get_system_prompt(task_type)
        
        messages = [
            Message.system(system_prompt),
            Message.user(prompt),
        ]
        
        return self.complete(
            messages=messages,
            task_type=task_type,
            user_id=user_id,
            task_id=task_id,
        )
    
    def _execute_with_retry(
        self,
        request: LLMRequest,
        model_config: ModelConfig,
        timeout: int,
    ) -> LLMResponse:
        """Execute request with retry and fallback."""
        fallback_chain = self._router.get_fallback_chain(request.model)
        last_error = None
        
        for model_name in fallback_chain:
            model_cfg = self._router.get_model(model_name) or MODELS.get("mock")
            
            for attempt in range(self.MAX_RETRIES):
                try:
                    return self._execute(request, model_cfg, timeout)
                except LLMRateLimitError:
                    # Rate limit - try next model
                    break
                except LLMTimeoutError as e:
                    last_error = e
                    # Timeout - retry with backoff
                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(2 ** attempt)
                    continue
                except LLMError as e:
                    last_error = e
                    break
        
        # All retries failed - use mock
        return self._mock_response(request, MODELS["mock"])
    
    def _execute(
        self,
        request: LLMRequest,
        model_config: ModelConfig,
        timeout: int,
    ) -> LLMResponse:
        """Execute single request."""
        if self._mock_mode or model_config.provider == LLMProvider.MOCK:
            return self._mock_response(request, model_config)
        
        # Real OpenAI API call
        if model_config.provider == LLMProvider.OPENAI:
            from .openai_provider import OpenAIProvider
            provider = OpenAIProvider(api_key=self._openai_api_key)
            return provider.complete(
                messages=request.messages,
                model=model_config.name,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
        
        # Fallback to mock for unsupported providers
        return self._mock_response(request, model_config)
    
    def _mock_response(
        self,
        request: LLMRequest,
        model_config: ModelConfig,
    ) -> LLMResponse:
        """Generate mock response."""
        # Extract user message
        user_message = ""
        for msg in request.messages:
            if msg.role == MessageRole.USER:
                user_message = msg.content
                break
        
        # Generate mock content based on context
        content = self._generate_mock_content(user_message)
        
        # Estimate tokens (rough: 1 token ≈ 4 chars)
        input_tokens = sum(len(m.content) // 4 for m in request.messages)
        output_tokens = len(content) // 4
        
        return LLMResponse(
            content=content,
            model=model_config.name,
            provider=model_config.provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=model_config.calculate_cost(input_tokens, output_tokens),
            finish_reason="stop",
        )
    
    def _generate_mock_content(self, user_message: str) -> str:
        """Generate mock content based on user message."""
        user_lower = user_message.lower()
        
        if "analyze" in user_lower or "analysis" in user_lower:
            return f"""Analysis of the request:

1. **Main Topic**: The user is asking about: {user_message[:50]}...
2. **Key Points**: This requires careful consideration of multiple factors.
3. **Recommendation**: A structured approach would be most effective.

This analysis provides a foundation for further action."""

        elif "research" in user_lower or "search" in user_lower:
            return f"""Research Findings:

Based on available information about "{user_message[:30]}...":

1. **Key Finding 1**: Significant developments in this area.
2. **Key Finding 2**: Multiple perspectives exist on this topic.
3. **Key Finding 3**: Recent trends indicate growing interest.

Sources consulted: Various reliable sources."""

        elif "draft" in user_lower or "post" in user_lower or "write" in user_lower:
            return f"""📝 Here's your content:

{user_message[:100]}...

This is engaging, well-structured content that addresses the key points.

#relevant #hashtags #content"""

        elif "summar" in user_lower:
            return f"""Summary:

**Key Points:**
- Main idea from the content
- Supporting detail 1
- Supporting detail 2

**Takeaway**: The content discusses important aspects of {user_message[:30]}..."""

        else:
            return f"""I've processed your request: "{user_message[:50]}..."

Here's my response:

This is a comprehensive answer that addresses your question. The key points are:
1. First important point
2. Second relevant detail
3. Actionable recommendation

Let me know if you need any clarification."""

    def estimate_cost(
        self,
        messages: List[Message],
        model: Optional[str] = None,
        max_tokens: int = 1000,
    ) -> float:
        """
        Estimate cost for a request.
        
        Args:
            messages: Messages to send
            model: Model name
            max_tokens: Expected max output tokens
            
        Returns:
            Estimated cost in USD
        """
        model_config = self._router.get_model(model) if model else MODELS.get("gpt-4o-mini")
        
        input_tokens = sum(len(m.content) // 4 for m in messages)
        return model_config.calculate_cost(input_tokens, max_tokens)
