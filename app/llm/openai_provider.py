"""
Yadro v0 - OpenAI Provider

Real OpenAI API integration.
"""
import os
import json
import urllib.request
import urllib.error
from typing import List, Optional

from .models import LLMResponse, Message, MessageRole, LLMProvider, MODELS


class OpenAIProvider:
    """OpenAI API provider."""
    
    API_URL = "https://api.openai.com/v1/chat/completions"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required")
    
    def complete(
        self,
        messages: List[Message],
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> LLMResponse:
        """Call OpenAI API."""
        
        # Convert messages to OpenAI format
        openai_messages = []
        for msg in messages:
            role = msg.role.value
            if role == "system":
                openai_messages.append({"role": "system", "content": msg.content})
            elif role == "user":
                openai_messages.append({"role": "user", "content": msg.content})
            elif role == "assistant":
                openai_messages.append({"role": "assistant", "content": msg.content})
        
        # Prepare request
        data = {
            "model": model,
            "messages": openai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        # Make request
        req = urllib.request.Request(
            self.API_URL,
            data=json.dumps(data).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            raise Exception(f"OpenAI API error {e.code}: {error_body}")
        
        # Parse response
        choice = result["choices"][0]
        usage = result.get("usage", {})
        
        # Get model config for pricing
        model_config = MODELS.get(model, MODELS.get("gpt-4o-mini"))
        
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        
        return LLMResponse(
            content=choice["message"]["content"],
            model=model,
            provider=LLMProvider.OPENAI,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=model_config.calculate_cost(input_tokens, output_tokens),
            finish_reason=choice.get("finish_reason", "stop"),
        )
