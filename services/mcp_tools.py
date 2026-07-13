"""MCP (Model Context Protocol) tool integration.

Connects to MCP servers and exposes their tools in OpenAI-format,
so any OpenAI-compatible client (or Gemini via conversion) can use them.
"""

import asyncio
import json
import logging
from contextlib import AsyncExitStack

from config import get_settings

logger = logging.getLogger(__name__)


class McpToolManager:
    """Manages connections to MCP servers and provides OpenAI-format tools.

    Usage:
        async with McpToolManager() as mgr:
            tools = mgr.get_openai_tools()
            # ... AI returns tool_call ...
            result = await mgr.execute_tool(call["function"]["name"], call["function"]["arguments"])
    """

    def __init__(self) -> None:
        self._exit_stack = AsyncExitStack()
        self._servers: list = []  # connected MCP server sessions
        self._tool_map: dict[str, tuple] = {}  # name -> (session, tool_info)

    async def __aenter__(self):
        await self.connect_all()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._exit_stack.aclose()

    async def connect_all(self):
        """Connect to all configured MCP servers."""
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except ImportError:
            raise RuntimeError(
                "mcp SDK not installed. Run: pip install mcp>=1.0.0"
            )

        settings = get_settings()
        for server in settings.mcp_servers:
            try:
                await self._connect_one(server, sse_client, stdio_client)
            except Exception:
                logger.exception("Failed to connect MCP server: %s", server)

    async def _connect_one(self, server_conf: dict, sse_client, stdio_client):
        """Connect a single MCP server and register its tools."""
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        transport_type = server_conf.get("type", "sse")
        headers = server_conf.get("headers")

        if transport_type == "sse":
            url = server_conf["url"]
            read, write = await self._exit_stack.enter_async_context(
                sse_client(url)
            )
        elif transport_type == "streamable_http":
            url = server_conf["url"]
            # streamablehttp_client returns (read, write, get_session_id)
            read, write, _ = await self._exit_stack.enter_async_context(
                streamablehttp_client(url, headers=headers)
            )
        elif transport_type == "stdio":
            params = StdioServerParameters(
                command=server_conf["command"],
                args=server_conf.get("args", []),
                env=server_conf.get("env"),
            )
            read, write = await self._exit_stack.enter_async_context(
                stdio_client(params)
            )
        else:
            logger.warning("Unknown MCP transport type: %s", transport_type)
            return
        session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await session.initialize()

        # List and register tools
        tools_list = await session.list_tools()
        for tool in tools_list.tools:
            self._tool_map[tool.name] = (session, tool)
            logger.info("Registered MCP tool: %s from %s", tool.name, transport_type)

        self._servers.append(session)
        logger.info("Connected MCP server: %s (%d tools)", server_conf, len(tools_list.tools))

    def get_openai_tools(self) -> list[dict]:
        """Get all MCP tools in OpenAI function calling format.

        Returns:
            List of tool definitions compatible with OpenAI's `tools` parameter.
        """
        tools = []
        for name, (_, tool_info) in self._tool_map.items():
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool_info.description or "",
                        "parameters": tool_info.inputSchema or {"type": "object", "properties": {}},
                    },
                }
            )
        return tools

    async def execute_tool(self, name: str, arguments_json: str) -> str:
        """Execute an MCP tool by name.

        Args:
            name: Tool name.
            arguments_json: JSON string of arguments.

        Returns:
            Tool execution result as string.
        """
        if name not in self._tool_map:
            return json.dumps({"error": f"Tool '{name}' not found"})

        session, _ = self._tool_map[name]
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError:
            args = {}

        result = await session.call_tool(name, args)
        # Extract text content from MCP response
        parts = []
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
        return "\n".join(parts) if parts else json.dumps(
            {"error": "Empty tool response"}, ensure_ascii=False
        )

    @property
    def has_tools(self) -> bool:
        return len(self._tool_map) > 0
