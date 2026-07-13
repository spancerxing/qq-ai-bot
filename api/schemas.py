"""Request/response schemas for the FastAPI HTTP API."""

from typing import Literal

from pydantic import BaseModel, Field

# M4: lock down role to the OpenAI-defined set to prevent prompt-injection
#     via exotic roles (e.g. "function", "tool", "developer").
MessageRole = Literal["user", "assistant", "system"]


class ChatMessage(BaseModel):
    role: MessageRole = Field(description="user, assistant, or system")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = Field(default=None, description="Optional model override")


class ChatResponse(BaseModel):
    reply: str
    model: str


class HealthResponse(BaseModel):
    status: str
    qq_connected: bool
    qq_configured: bool


class ModelsResponse(BaseModel):
    models: list[dict]


class SessionsResponse(BaseModel):
    active_sessions: int
