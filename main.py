"""Main entry point: FastAPI app with QQ Bot WebSocket client lifecycle."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from api.router import init_router, router
from config import get_settings
from plugins.c2c_ai import C2CAiHandler
from plugins.group_ai import GroupAiHandler
from plugins.group_ai.context import SessionManager
from qq_bot.client import QQBotClient
from qq_bot.messages import MessageSender
from services.ai_router import AiRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Global instances
bot_client = QQBotClient()
ai_router = AiRouter()
session_manager = SessionManager()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage QQ Bot WS connection lifecycle."""
    settings = get_settings()

    # Build dependencies
    token_manager = bot_client.token_manager
    message_sender = MessageSender(token_manager)
    group_handler = GroupAiHandler(message_sender, ai_router)
    c2c_handler = C2CAiHandler(message_sender, ai_router)

    # Register event handlers
    bot_client.on_group_at_message(group_handler.handle)
    bot_client.on_c2c_message(c2c_handler.handle)

    # Init router
    init_router(ai_router, bot_client, session_manager)

    # Initialize MCP connections once at startup
    await ai_router.init_mcp()

    # Start bot client in background
    if settings.qq_enabled:
        bot_task = asyncio_run_safe(bot_client.start())
        logger.info("QQ Bot client starting in background...")
    else:
        bot_task = None
        logger.warning("QQ Bot not configured, running in HTTP-only mode")

    yield

    # Cleanup
    await ai_router.close_mcp()
    if bot_task:
        await bot_client.stop()


def asyncio_run_safe(coro) -> asyncio.Task:
    """Fire-and-forget an async task."""
    return asyncio.create_task(coro)


app = FastAPI(title="QQ AI Bot", lifespan=lifespan)
app.include_router(router, prefix="/api")


@app.get("/")
async def root() -> dict:
    return {"service": "qq-ai-bot", "docs": "/docs"}


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
