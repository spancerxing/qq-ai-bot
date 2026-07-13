"""Send messages via QQ Bot HTTP API."""

import logging

import certifi
import httpx

from qq_bot.gateway import TokenManager

logger = logging.getLogger(__name__)

API_BASE = "https://api.sgroup.qq.com"


class MessageSendError(RuntimeError):
    """Raised when a QQ Bot HTTP API call fails."""


# Module-level shared client for connection reuse
_client: httpx.AsyncClient | None = None


def _get_httpx_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(verify=certifi.where(), timeout=10.0)
    return _client


class MessageSender:
    """Send messages to QQ groups or users via HTTP API."""

    def __init__(self, token_manager: TokenManager) -> None:
        self._token_mgr = token_manager

    async def _get_headers(self) -> dict[str, str]:
        token = await self._token_mgr.get_token()
        return {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }

    async def send_group_message(
        self,
        group_openid: str,
        content: str,
        msg_id: str | None = None,
        msg_seq: int = 1,
    ) -> dict:
        """Send a markdown message to a group.

        Args:
            group_openid: The group's open ID.
            content: Markdown content to send.
            msg_id: If provided, this is a passive reply (within 5 min window).
            msg_seq: Sequence number for deduplication.
        """
        headers = await self._get_headers()
        payload: dict = {
            "msg_type": 2,  # markdown
            "markdown": {
                "content": content,
            },
            "msg_seq": msg_seq,
        }
        if msg_id:
            payload["msg_id"] = msg_id

        url = f"{API_BASE}/v2/groups/{group_openid}/messages"
        client = _get_httpx_client()
        resp = await client.post(url, json=payload, headers=headers)
        if not resp.is_success:
            logger.error(
                "Failed to send group message: %s %s", resp.status_code, resp.text
            )
            return {}
        logger.info("Sent message to group %s: %s", group_openid, content[:50])
        return resp.json() if resp.content else {}

    async def send_c2c_message(
        self,
        user_openid: str,
        content: str,
        msg_id: str | None = None,
        msg_seq: int = 1,
    ) -> dict:
        """Send a markdown message to a user (private chat)."""
        headers = await self._get_headers()
        payload: dict = {
            "msg_type": 2,  # markdown
            "markdown": {
                "content": content,
            },
            "msg_seq": msg_seq,
        }
        if msg_id:
            payload["msg_id"] = msg_id

        url = f"{API_BASE}/v2/users/{user_openid}/messages"
        client = _get_httpx_client()
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json() if resp.content else {}
