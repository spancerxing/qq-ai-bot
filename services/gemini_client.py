"""Google Gemini API adapter using the official google-genai SDK."""

import json
import logging
import random

from config import get_settings

logger = logging.getLogger(__name__)


class GeminiClient:
    """Client for Google Gemini API using the official google-genai SDK.

    Supports multi-key load balancing and MCP tool calling.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._cached_clients: dict[str, object] = {}

    @staticmethod
    def _clean_schema(schema: dict | None) -> dict | None:
        """Remove JSON Schema fields not supported by Gemini FunctionDeclaration.

        Gemini rejects $schema, propertyNames, if/then/else, additionalProperties, etc.
        Strip these recursively while preserving the core type/properties/required structure.
        """
        if not schema or not isinstance(schema, dict):
            return schema
        cleaned = {}
        allowed_top = {"type", "properties", "required", "description", "enum", "items", "anyOf", "allOf"}
        for key, value in schema.items():
            if key not in allowed_top and not key.startswith("x-"):
                continue
            if isinstance(value, dict):
                cleaned[key] = GeminiClient._clean_schema(value)
            elif isinstance(value, list):
                cleaned[key] = [
                    GeminiClient._clean_schema(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                cleaned[key] = value
        return cleaned

    def _get_client(self):
        """Create a Gemini client with a randomly selected key.

        Supports custom base_url via AI_GEMINI_BASE_URL for proxy setups.
        """
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise RuntimeError(
                "google-genai SDK not installed. Run: pip install google-genai>=1.0.0"
            )

        keys = self._settings.ai_gemini_keys
        if not keys:
            raise RuntimeError("No Gemini API keys configured")
        key = random.choice(keys)

        # Cache client per key to reuse connections
        if key not in self._cached_clients:
            kwargs: dict = {"api_key": key}
            # Support custom base URL (e.g. a Gemini-protocol proxy)
            if self._settings.ai_gemini_base_url:
                kwargs["http_options"] = types.HttpOptions(
                    baseUrl=self._settings.ai_gemini_base_url
                )
            self._cached_clients[key] = genai.Client(**kwargs)
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
        """Send a chat request to Gemini.

        Args:
            messages: List of {"role": "user"|"model", "content": "..."}.
                      System prompts should be in system_instruction.
            model: Model name. Defaults to settings.
            max_tokens: Max response tokens.
            temperature: Sampling temperature.
            presence_penalty: Presence penalty.
            tools: OpenAI-format tool definitions list.

        Returns:
            Dict with keys:
                - "content": str | None — the response text
                - "tool_calls": list[dict] | None — tool call requests
        """
        from google.genai import types

        client = self._get_client()
        model_name = model or self._settings.ai_default_model

        # Convert OpenAI-format messages to Gemini format
        system_instruction = None
        gemini_contents: list[types.Content] = []

        for msg in messages:
            content = msg.get("content", "")
            if not content:
                continue

            if msg["role"] == "system":
                system_instruction = content
            elif msg["role"] == "user":
                gemini_contents.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=content)])
                )
            elif msg["role"] == "assistant":
                gemini_contents.append(
                    types.Content(
                        role="model", parts=[types.Part.from_text(text=content)]
                    )
                )

        # Convert OpenAI tools format to Gemini function declarations
        gemini_tools = None
        if tools:
            function_declarations = []
            for tool in tools:
                if tool.get("type") == "function":
                    func = tool["function"]
                    params = self._clean_schema(func.get("parameters"))
                    function_declarations.append(
                        types.FunctionDeclaration(
                            name=func["name"],
                            description=func.get("description", ""),
                            parameters=params,
                        )
                    )
            if function_declarations:
                gemini_tools = [types.Tool(function_declarations=function_declarations)]

        # Generation config
        config_kwargs: dict = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "system_instruction": system_instruction,
            "tools": gemini_tools,
        }
        if gemini_tools:
            config_kwargs["tool_config"] = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.AUTO,
                )
            )
        config = types.GenerateContentConfig(**config_kwargs)

        # Use google-genai's native async client (no thread pool needed)
        response = await client.aio.models.generate_content(
            model=model_name,
            contents=gemini_contents,
            config=config,
        )

        # Parse response
        response_text = response.text or ""

        # Extract function calls (tool calls)
        tool_calls = None
        if response.function_calls:
            tool_calls = []
            for fc in response.function_calls:
                args_dict = {}
                if fc.args:
                    # fc.args is a protobuf MapComposite, convert to dict
                    args_dict = dict(fc.args)
                tool_calls.append(
                    {
                        "id": fc.id or f"call_{fc.name}",
                        "function": {
                            "name": fc.name,
                            "arguments": json.dumps(args_dict, ensure_ascii=False),
                        },
                    }
                )

        return {
            "content": response_text,
            "tool_calls": tool_calls,
        }
