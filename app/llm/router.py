"""
Yadro v0 - Model Router

Selects appropriate model based on task requirements.
"""
from typing import Optional, List
from dataclasses import dataclass

from .models import ModelConfig, MODELS, LLMProvider


@dataclass
class RouterConfig:
    """Router configuration."""
    # Default models by tier
    primary_model: str = "gpt-4o-mini"
    fallback_model: str = "mock"
    
    # Budget thresholds (USD)
    max_cost_per_call: float = 0.10
    max_cost_per_task: float = 1.00
    
    # Context size thresholds
    large_context_threshold: int = 50000
    
    # Task type preferences
    task_model_overrides: dict = None
    
    def __post_init__(self):
        if self.task_model_overrides is None:
            self.task_model_overrides = {}


class ModelRouter:
    """
    Routes requests to appropriate models.
    
    Selection criteria:
    - Task type
    - Context size
    - Budget constraints
    - Model capabilities
    """
    
    def __init__(self, config: Optional[RouterConfig] = None):
        """
        Initialize router.
        
        Args:
            config: Router configuration
        """
        self.config = config or RouterConfig()
        self._models = MODELS.copy()
    
    def select_model(
        self,
        task_type: str = "general",
        context_size: int = 0,
        requires_vision: bool = False,
        requires_json: bool = False,
        budget_remaining: Optional[float] = None,
    ) -> ModelConfig:
        """
        Select best model for request.
        
        Args:
            task_type: Type of task
            context_size: Estimated context size in tokens
            requires_vision: Whether vision is needed
            requires_json: Whether structured output is needed
            budget_remaining: Remaining budget (USD)
            
        Returns:
            Selected ModelConfig
        """
        # Check task-specific override
        if task_type in self.config.task_model_overrides:
            model_name = self.config.task_model_overrides[task_type]
            if model_name in self._models:
                return self._models[model_name]
        
        # Get candidate models
        candidates = self._get_candidates(
            context_size=context_size,
            requires_vision=requires_vision,
            requires_json=requires_json,
        )
        
        if not candidates:
            # Fallback to mock if nothing fits
            return self._models.get(self.config.fallback_model, self._models["mock"])
        
        # Filter by budget if specified
        if budget_remaining is not None:
            candidates = [
                m for m in candidates
                if self._estimate_cost(m, context_size) <= budget_remaining
            ]
            
            if not candidates:
                return self._models.get(self.config.fallback_model, self._models["mock"])
        
        # Return cheapest suitable model
        candidates.sort(key=lambda m: m.input_price_per_million)
        return candidates[0]
    
    def _get_candidates(
        self,
        context_size: int,
        requires_vision: bool,
        requires_json: bool,
    ) -> List[ModelConfig]:
        """Get candidate models matching requirements."""
        candidates = []
        
        for model in self._models.values():
            # Skip mock for real selection
            if model.provider == LLMProvider.MOCK:
                continue
            
            # Check context size
            if context_size > model.max_context_tokens:
                continue
            
            # Check vision requirement
            if requires_vision and not model.supports_vision:
                continue
            
            # Check JSON requirement
            if requires_json and not model.supports_json:
                continue
            
            candidates.append(model)
        
        return candidates
    
    def _estimate_cost(self, model: ModelConfig, context_size: int) -> float:
        """Estimate cost for a request."""
        # Assume output is ~25% of input
        estimated_output = min(context_size // 4, model.max_output_tokens)
        return model.calculate_cost(context_size, estimated_output)
    
    def get_fallback_chain(self, primary: str) -> List[str]:
        """
        Get fallback chain for a model.
        
        Args:
            primary: Primary model name
            
        Returns:
            List of model names to try in order
        """
        chain = [primary]
        
        # Add fallbacks based on provider
        if primary in self._models:
            provider = self._models[primary].provider
            
            if provider == LLMProvider.OPENAI:
                if primary != "gpt-4o-mini":
                    chain.append("gpt-4o-mini")
            elif provider == LLMProvider.ANTHROPIC:
                if primary != "claude-3-5-haiku":
                    chain.append("claude-3-5-haiku")
        
        # Always add mock as last resort
        chain.append("mock")
        
        return chain
    
    def register_model(self, config: ModelConfig) -> None:
        """Register a custom model."""
        self._models[config.name] = config
    
    def get_model(self, name: str) -> Optional[ModelConfig]:
        """Get model config by name."""
        return self._models.get(name)
    
    def list_models(self) -> List[str]:
        """List available model names."""
        return list(self._models.keys())


# Global router
router = ModelRouter()
