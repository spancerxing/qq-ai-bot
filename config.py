"""Configuration management using pydantic-settings."""

import json
import os
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, DotEnvSettingsSource, SettingsConfigDict


class RawDotEnvSettingsSource(DotEnvSettingsSource):
    """Custom dotenv source that skips JSON decoding for list fields.

    The default DotEnvSettingsSource tries json.loads() on any value for
    complex types (list, dict, etc.) before validators run. This fails for
    comma-separated strings like 'sk-key1,sk-key2'. We intercept those and
    pass them through as raw strings so our model_post_init can split them.
    """

    # Field names that should be treated as comma-separated strings, not JSON
    RAW_LIST_FIELDS = {"AI_OPENAI_KEYS", "AI_GEMINI_KEYS", "AI_CLAUDE_KEYS"}

    def decode_complex_value(self, field_name: str, field: Any, value: str) -> Any:
        # If this is one of our comma-separated fields, return the raw string
        env_key = field_name.upper()
        if env_key in self.RAW_LIST_FIELDS:
            return value
        return super().decode_complex_value(field_name, field, value)


def _split_keys(value: str) -> list[str]:
    """Split a comma-separated string into a list of trimmed, non-empty strings."""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # QQ Bot
    qq_app_id: str = Field(default="", alias="QQ_APP_ID")
    qq_app_secret: str = Field(default="", alias="QQ_APP_SECRET")

    # AI - OpenAI compatible (comma-separated keys in .env)
    ai_openai_keys: list[str] = Field(default_factory=list, alias="AI_OPENAI_KEYS")
    ai_openai_base_url: str = Field(
        default="https://api.openai.com/v1", alias="AI_OPENAI_BASE_URL"
    )

    # AI - Google Gemini (comma-separated keys in .env)
    ai_gemini_keys: list[str] = Field(default_factory=list, alias="AI_GEMINI_KEYS")
    ai_gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        alias="AI_GEMINI_BASE_URL",
    )

    # AI - Anthropic Claude (comma-separated keys in .env)
    ai_claude_keys: list[str] = Field(default_factory=list, alias="AI_CLAUDE_KEYS")

    # MCP servers (JSON array in .env)
    # Example: [{"type":"sse","url":"http://localhost:3000/sse"}]
    #          [{"type":"stdio","command":"python","args":["server.py"]}]
    mcp_servers: list[dict] = Field(default_factory=list, alias="MCP_SERVERS")

    # AI default
    ai_default_model: str = Field(default="gpt-4o-mini", alias="AI_DEFAULT_MODEL")
    ai_system_prompt: str = Field(
        default="你是一个友好、有用的AI助手。请用简洁的中文回答。",
        alias="AI_SYSTEM_PROMPT",
    )
    ai_max_tokens: int = Field(default=2048, alias="AI_MAX_TOKENS")
    ai_temperature: float = Field(default=0.7, alias="AI_TEMPERATURE")
    ai_presence_penalty: float = Field(default=0.0, alias="AI_PRESENCE_PENALTY")

    # Session
    session_timeout: int = Field(default=30, alias="SESSION_TIMEOUT")
    session_max_history: int = Field(default=10, alias="SESSION_MAX_HISTORY")

    # Server
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    @property
    def qq_enabled(self) -> bool:
        return bool(self.qq_app_id and self.qq_app_secret)

    @field_validator("ai_openai_keys", "ai_claude_keys", "ai_gemini_keys", mode="before")
    @classmethod
    def split_keys(cls, v: Any) -> list[str]:
        """Convert comma-separated string to list before pydantic validation."""
        if isinstance(v, list):
            return v
        if not v or not isinstance(v, str):
            return []
        return _split_keys(v)

    @field_validator("mcp_servers", mode="before")
    @classmethod
    def parse_mcp_servers(cls, v: Any) -> list[dict]:
        """Parse MCP servers from JSON string or list."""
        if isinstance(v, list):
            return v
        if not v or not isinstance(v, str):
            return []
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return []

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type,
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple:
        """Replace the default dotenv source with our raw-string variant."""
        return (
            init_settings,
            env_settings,
            RawDotEnvSettingsSource(
                settings_cls,
                env_file=cls.model_config.get("env_file"),
                env_file_encoding=cls.model_config.get("env_file_encoding"),
            ),
            file_secret_settings,
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
