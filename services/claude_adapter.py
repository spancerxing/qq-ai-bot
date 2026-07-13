"""Anthropic Claude API adapter with multi-key support."""

import logging
import random

import anthropic

from config import get_settings

logger = logging.getLogger(__name__)


class ClaudeClient:
    """Client for Anthropic Claude API with multi-key support."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._cached_clients: dict[str, anthropic.AsyncAnthropic] = {}

    def _get_client(self) -> anthropic.AsyncAnthropic:
        """Get or create a Claude client, cached per key."""
        keys = self._settings.ai_claude_keys
        if not keys:
            raise RuntimeError("No Claude API keys configured")
        key = random.choice(keys)
        if key not in self._cached_clients:
            self._cached_clients[key] = anthropic.AsyncAnthropic(api_key=key)
        return self._cached_clients[key]

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        presence_penalty: float = 0.0,
        tools: list[dict] | None = None,
    ) -> str:
        """Send a chat completion request and return the response text.
        tools is accepted for interface compatibility but not yet implemented for Claude."""
        client = self._get_client()
        model_name = model or "claude-3-5-sonnet-20241022"

        # Separate system message if present
        system_msg = None
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                chat_messages.append(msg)

        kwargs: dict = {
            "model": model_name,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "presence_penalty": presence_penalty,
            "messages": chat_messages,  # type: ignore[arg-type]
        }
        if system_msg:
            kwargs["system"] = system_msg

        response = await client.messages.create(**kwargs)
        # Extract text from content blocks
        text_parts = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        return "".join(text_parts)
