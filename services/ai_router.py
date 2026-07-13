"""AI service router: dispatches requests to the correct provider based on model name."""

import asyncio
import json
import logging

from config import get_settings
from services.claude_adapter import ClaudeClient
from services.gemini_client import GeminiClient
from services.mcp_tools import McpToolManager
from services.openai_compat import OpenAICompatibleClient

logger = logging.getLogger(__name__)

# Maximum number of tool call rounds per request (keeps within QQ 5-min window)
MCP_MAX_TOOL_ROUNDS = 2


class AiRouter:
    """Routes AI chat requests to the appropriate provider.

    Model name prefixes determine the provider:
    - gpt-*, deepseek-*, qwen-*, etc. -> OpenAI-compatible (official SDK)
    - gemini-*                   -> Google Gemini (official SDK)
    - claude-*                   -> Anthropic Claude
    """

    def __init__(self) -> None:
        self._openai = OpenAICompatibleClient()
        self._gemini = GeminiClient()
        self._claude = ClaudeClient()
        # MCP is initialized once at startup via init_mcp() and reused
        self._mcp_mgr: McpToolManager | None = None
        self._mcp_tools: list[dict] = []

    async def init_mcp(self) -> None:
        """Connect to MCP servers once at startup and cache tools."""
        settings = get_settings()
        if not settings.mcp_servers:
            return
        try:
            mgr = McpToolManager()
            await mgr.connect_all()
            if mgr.has_tools:
                self._mcp_tools = mgr.get_openai_tools()
                self._mcp_mgr = mgr
                logger.info("MCP initialized with %d tools", len(self._mcp_tools))
            else:
                logger.info("MCP servers connected but exposed no tools")
        except BaseException:
            logger.exception("MCP initialization failed, continuing without tools")

    async def close_mcp(self) -> None:
        """Clean up MCP connections on shutdown."""
        if self._mcp_mgr:
            await self._mcp_mgr.__aexit__(None, None, None)

    @staticmethod
    def _time_context_fragment() -> str:
        """Build a time+location context string injected into every system prompt."""
        from datetime import datetime, timezone, timedelta

        # Assume China time (UTC+8) since this is a QQ bot
        tz_cst = timezone(timedelta(hours=8))
        now = datetime.now(tz_cst)
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekdays[now.weekday()]

        return (
            "---\n"
            f"当前时间：{now.strftime('%Y年%m月%d日 %H:%M:%S')} ({weekday})\n"
            f"时区：北京时间 (UTC+8)\n"
            "---"
        )

    def _resolve_provider(self, model: str | None) -> str:
        """Determine provider from model name."""
        if not model:
            model = get_settings().ai_default_model

        if model.startswith("claude-"):
            return "claude"
        if model.startswith("gemini-"):
            return "gemini"
        return "openai"

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        presence_penalty: float | None = None,
        use_mcp: bool = True,
    ) -> str:
        """Route a chat request to the appropriate AI provider.

        Args:
            messages: List of {"role": "user"|"assistant"|"system", "content": "..."}
            model: Model name. If None, uses the default from config.
            system_prompt: System prompt. If None, uses the default from config.
            max_tokens: Max response tokens. If None, uses the default from config.
            temperature: Sampling temperature. If None, uses the default from config.
            presence_penalty: Presence penalty (-2 to 2). If None, uses the default from config.
            use_mcp: Whether to enable MCP tools if configured.

        Returns:
            The AI response text.
        """
        settings = get_settings()
        model_name = model or settings.ai_default_model
        system = system_prompt or settings.ai_system_prompt
        tokens = max_tokens or settings.ai_max_tokens
        temp = temperature or settings.ai_temperature
        penalty = presence_penalty if presence_penalty is not None else settings.ai_presence_penalty
        provider = self._resolve_provider(model_name)

        logger.info("Routing to provider=%s model=%s temp=%s max_tokens=%s penalty=%s", provider, model_name, temp, tokens, penalty)

        # Dynamically inject current time & location into system prompt
        time_ctx = self._time_context_fragment()
        system = f"{time_ctx}\n\n{system}" if system else time_ctx

        # Prepend system prompt if provided and not already in messages
        if system and not any(m.get("role") == "system" for m in messages):
            messages = [{"role": "system", "content": system}, *messages]

        # Use pre-initialized MCP tools if available
        if use_mcp and self._mcp_tools and self._mcp_mgr:
            return await self._chat_with_tools(
                self._mcp_mgr, messages, model_name, provider,
                tokens, temp, penalty, self._mcp_tools,
            )

        # No MCP — direct call
        result = await self._direct_chat(
            messages, model_name, provider, tokens, temp, penalty,
        )
        return result["content"] or ""

    async def _direct_chat(
        self,
        messages: list[dict],
        model_name: str,
        provider: str,
        tokens: int,
        temp: float,
        penalty: float,
        tools: list[dict] | None = None,
    ) -> dict:
        """Make a direct chat call, optionally with tools."""
        if provider == "claude":
            content = await self._claude.chat(
                messages, model=model_name, max_tokens=tokens,
                temperature=temp, presence_penalty=penalty, tools=tools,
            )
            return {"content": content, "tool_calls": None}
        elif provider == "gemini":
            return await self._gemini.chat(
                messages, model=model_name, max_tokens=tokens,
                temperature=temp, presence_penalty=penalty, tools=tools,
            )
        else:
            return await self._openai.chat(
                messages, model=model_name, max_tokens=tokens,
                temperature=temp, presence_penalty=penalty, tools=tools,
            )

    async def _chat_with_tools(
        self,
        mgr: McpToolManager,
        messages: list[dict],
        model_name: str,
        provider: str,
        tokens: int,
        temp: float,
        penalty: float,
        tools: list[dict],
    ) -> str:
        """Chat with MCP tool support — tool loop then forced text response."""
        seen_calls: set[str] = set()
        for _round in range(MCP_MAX_TOOL_ROUNDS):
            result = await self._direct_chat(
                messages, model_name, provider, tokens, temp, penalty,
                tools=tools,
            )

            # No tool calls → AI answered directly
            if not result.get("tool_calls"):
                return result["content"] or ""

            # Break if same call repeated (model is stuck looping)
            call_key = "|".join(
                f"{c['function']['name']}({c['function']['arguments']})"
                for c in result["tool_calls"]
            )
            if call_key in seen_calls:
                logger.warning("Breaking tool loop: repetitive call detected")
                break
            seen_calls.add(call_key)

            # Append assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": result["content"] or "",
                "tool_calls": result["tool_calls"],
            })

            # Execute all tool calls in parallel
            async def _exec_call(call: dict) -> tuple[str, str]:
                name = call["function"]["name"]
                args = call["function"]["arguments"]
                logger.info("Executing MCP tool: %s(%s)", name, args[:100])
                try:
                    res = await mgr.execute_tool(name, args)
                except Exception as e:
                    # M8: don't leak MCP internals (paths, network errors, env)
                    #     back into the AI context — log full error, return generic.
                    res = json.dumps(
                        {"error": f"工具 {name} 调用失败"}, ensure_ascii=False
                    )
                    logger.warning("MCP tool %s failed: %s", name, e)
                return call.get("id", ""), res

            exec_results = await asyncio.gather(
                *[_exec_call(c) for c in result["tool_calls"]]
            )
            for call_id, tool_result in exec_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": tool_result,
                })

            # Got results → make one final call WITHOUT tools to force text reply
            final = await self._direct_chat(
                messages, model_name, provider, tokens, temp, penalty,
            )
            if final.get("content"):
                return final["content"]

        # Fallback: no text content generated
        return "搜索结果已获取，让我整理一下～ 😊"

    def list_models(self) -> list[dict]:
        """List all available models from configured providers."""
        models = []
        settings = get_settings()

        if settings.ai_openai_keys:
            models.extend([
                {"id": "gpt-4o", "provider": "openai"},
                {"id": "gpt-4o-mini", "provider": "openai"},
                {"id": "deepseek-chat", "provider": "openai-compat"},
            ])

        if settings.ai_gemini_keys:
            models.extend([
                {"id": "gemini-2.5-pro", "provider": "gemini"},
                {"id": "gemini-2.0-flash", "provider": "gemini"},
            ])

        if settings.ai_claude_keys:
            models.extend([
                {"id": "claude-3-5-sonnet-20241022", "provider": "claude"},
                {"id": "claude-3-haiku-20240307", "provider": "claude"},
            ])

        return models
