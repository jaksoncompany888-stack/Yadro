"""
Yadro v0 - Anthropic Provider

Real Anthropic Claude API integration.
"""
import os
import json
import urllib.request
import urllib.error
from typing import List, Optional

from .models import LLMResponse, Message, MessageRole, LLMProvider, MODELS


class AnthropicProvider:
    """Anthropic Claude API provider."""

    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            print("[Anthropic] ERROR: No API key provided!")
            raise ValueError("Anthropic API key required")
        print(f"[Anthropic] Initialized with key: {self.api_key[:20]}...")

    def complete(
        self,
        messages: List[Message],
        model: str = "claude-sonnet-4-20250514",
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> LLMResponse:
        """Call Anthropic API."""
        print(f"[Anthropic] complete() called with model={model}")

        # Separate system prompt from messages
        system_prompt = None
        anthropic_messages = []

        for msg in messages:
            role = msg.role.value
            if role == "system":
                system_prompt = msg.content
            elif role == "user":
                anthropic_messages.append({"role": "user", "content": msg.content})
            elif role == "assistant":
                anthropic_messages.append({"role": "assistant", "content": msg.content})

        # Prepare request
        data = {
            "model": model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Add system prompt if present
        if system_prompt:
            data["system"] = system_prompt

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
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
            print(f"[Anthropic] Sending request to API...")
            with urllib.request.urlopen(req, timeout=90) as response:
                result = json.loads(response.read().decode("utf-8"))
            print(f"[Anthropic] Got response, stop_reason={result.get('stop_reason')}")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            print(f"[Anthropic] HTTP Error {e.code}: {error_body}")
            raise Exception(f"Anthropic API error {e.code}: {error_body}")
        except Exception as e:
            print(f"[Anthropic] Request failed: {e}")
            raise

        # Parse response
        content = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        usage = result.get("usage", {})

        # Get model config for pricing
        # Map model name to our config
        model_key = "claude-sonnet-4" if "sonnet" in model else "claude-haiku-3-5"
        model_config = MODELS.get(model_key, MODELS.get("claude-sonnet-4"))

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        return LLMResponse(
            content=content,
            model=model,
            provider=LLMProvider.ANTHROPIC,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=model_config.calculate_cost(input_tokens, output_tokens),
            finish_reason=result.get("stop_reason", "end_turn"),
        )
