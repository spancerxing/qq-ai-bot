"""QQ Bot gateway: AccessToken management and gateway URL retrieval."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import certifi
import httpx

from config import get_settings

logger = logging.getLogger(__name__)

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_URL = "https://api.sgroup.qq.com/gateway"

# Shared httpx client factory with proper SSL certs
def _make_httpx_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=certifi.where(), timeout=10.0)


class TokenManager:
    """Manages QQ Bot AccessToken with automatic refresh."""

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def access_token(self) -> str | None:
        return self._access_token

    @property
    def is_valid(self) -> bool:
        if not self._access_token or not self._expires_at:
            return False
        # M6: use timezone-aware UTC (datetime.utcnow() is deprecated in 3.12+)
        return datetime.now(timezone.utc) < self._expires_at

    async def get_token(self) -> str:
        """Get a valid AccessToken, refreshing if necessary."""
        if self.is_valid:
            return self._access_token  # type: ignore[return-value]

        async with self._lock:
            # Double-check after acquiring lock
            if self.is_valid:
                return self._access_token  # type: ignore[return-value]

            await self._refresh()
            return self._access_token  # type: ignore[return-value]

    async def _refresh(self) -> None:
        """Fetch a new AccessToken from QQ API."""
        settings = get_settings()
        async with _make_httpx_client() as client:
            resp = await client.post(
                TOKEN_URL,
                json={
                    "appId": settings.qq_app_id,
                    "clientSecret": settings.qq_app_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if "access_token" not in data:
            raise RuntimeError(f"Failed to get access token: {data}")

        self._access_token = data["access_token"]
        # Token typically expires in 7200 seconds; refresh 5 min early
        expires_in = int(data.get("expires_in", 7200))
        # M6: timezone-aware UTC
        self._expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=expires_in - 300
        )
        logger.info("AccessToken refreshed, expires in %ds", expires_in)


async def get_gateway_url(token_manager: TokenManager) -> str:
    """Get the WebSocket gateway URL."""
    token = await token_manager.get_token()

    async with _make_httpx_client() as client:
        resp = await client.get(
            GATEWAY_URL,
            headers={"Authorization": f"QQBot {token}"},
        )
        resp.raise_for_status()
        data = resp.json()

    url = data.get("url")
    if not url:
        raise RuntimeError(f"Failed to get gateway URL: {data}")

    return url
