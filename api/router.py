"""FastAPI router for HTTP API endpoints."""

import logging

from fastapi import APIRouter, HTTPException

from api.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    ModelsResponse,
    SessionsResponse,
)
from config import get_settings
from plugins.group_ai.context import SessionManager
from qq_bot.client import QQBotClient
from services.ai_router import AiRouter

logger = logging.getLogger(__name__)

router = APIRouter()

# These will be set during startup
_ai_router: AiRouter | None = None
_bot_client: QQBotClient | None = None
_session_manager: SessionManager | None = None


def init_router(
    ai_router: AiRouter,
    bot_client: QQBotClient,
    session_manager: SessionManager,
) -> None:
    """Initialize references from main.py."""
    global _ai_router, _bot_client, _session_manager
    _ai_router = ai_router
    _bot_client = bot_client
    _session_manager = session_manager


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        qq_connected=_bot_client.is_connected if _bot_client else False,
        qq_configured=settings.qq_enabled,
    )


@router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    """List available AI models."""
    if not _ai_router:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return ModelsResponse(models=_ai_router.list_models())


@router.get("/sessions", response_model=SessionsResponse)
async def active_sessions() -> SessionsResponse:
    """Get active session count."""
    if not _session_manager:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return SessionsResponse(active_sessions=await _session_manager.active_count())


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Direct AI chat endpoint (bypasses QQ, useful for testing)."""
    if not _ai_router:
        raise HTTPException(status_code=503, detail="Service not initialized")

    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    try:
        reply = await _ai_router.chat(messages, model=request.model)
    except Exception:
        # H1: do not leak internal exception text (model names, API errors, key
        #     prefixes) — log full traceback for operators only.
        logger.exception("Chat error")
        raise HTTPException(status_code=500, detail="internal error") from None

    settings = get_settings()
    return ChatResponse(
        reply=reply,
        model=request.model or settings.ai_default_model,
    )
