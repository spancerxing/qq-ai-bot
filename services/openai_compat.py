"""OpenAI-compatible API adapter using the official OpenAI SDK.

Works with OpenAI, DeepSeek, Qwen, and any API that follows the OpenAI format.
Uses the openai SDK for native type safety, retry logic, and streaming support.
"""

import logging
import random

import openai

from config import get_settings

logger = logging.getLogger(__name__)


class OpenAICompatibleClient:
    """Client for OpenAI-compatible APIs using the official OpenAI SDK."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._cached_clients: dict[str, openai.AsyncOpenAI] = {}

    def _get_client(self) -> openai.AsyncOpenAI:
        """Get or create an async OpenAI client, cached per key."""
        keys = self._settings.ai_openai_keys
        if not keys:
            raise RuntimeError("No OpenAI-compatible API keys configured")
        key = random.choice(keys)
        if key not in self._cached_clients:
            self._cached_clients[key] = openai.AsyncOpenAI(
                api_key=key,
                base_url=self._settings.ai_openai_base_url,
                timeout=30.0,
            )
        return self._cached_clients[key]

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        presence_penalty: float = 0.0,
        tools: list[dict] | None = None,
    ) -> dict:
        """Send a chat completion request.

        Returns:
            Dict with keys:
                - "content": str | None — the response text
                - "tool_calls": list[dict] | None — tool call requests if any
                  Each item: {"id": str, "function": {"name": str, "arguments": str}}
        """
        client = self._get_client()
        model_name = model or self._settings.ai_default_model

        kwargs: dict = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "presence_penalty": presence_penalty,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0].message

        return {
            "content": choice.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in (choice.tool_calls or [])
            ],
        }
