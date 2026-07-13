"""QQ Bot WebSocket client with auto-reconnect and event dispatch."""

import asyncio
import json
import logging
import ssl
from collections.abc import Callable, Awaitable

import websockets
from websockets.asyncio.client import ClientConnection

from config import get_settings
from qq_bot.events import C2CMessageEvent, GroupAtMessageEvent
from qq_bot.gateway import TokenManager, get_gateway_url
from qq_bot.intents import GROUP_AND_C2C

logger = logging.getLogger(__name__)

# OpCode constants
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RECONNECT = 7
OP_RESUME = 6
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# Sentinel pushed onto the dispatch queue to signal the worker to stop.
_SHUTDOWN = object()

# Type alias for event handlers
EventHandler = Callable[[GroupAtMessageEvent], Awaitable[None]]
C2CHandler = Callable[[C2CMessageEvent], Awaitable[None]]


class QQBotClient:
    """WebSocket client for QQ Official Bot API.

    Handles connection lifecycle: connect, authenticate, heartbeat,
    reconnect (with resume), and event dispatch.

    Architecture note — why receive and dispatch are decoupled:
    The receive loop only reads WebSocket frames and enqueues DISPATCH
    events.  A *separate* dispatch worker consumes the queue and invokes
    handlers.  This is critical because an AI handler (especially one that
    uses MCP tools) can block for tens of seconds.  If dispatch ran inside
    the receive loop, the socket would go unread during that time —
    heartbeat ACKs would pile up, server RECONNECT requests (Op 7) would
    be missed, and the websockets library would eventually close the
    connection once its internal ``max_queue`` overflows.  Keeping the
    receive loop free-running guarantees the connection stays healthy
    regardless of how long any single handler takes.
    """

    def __init__(self) -> None:
        self._token_mgr = TokenManager()
        self._ws: ClientConnection | None = None
        self._heartbeat_interval: float = 30.0
        self._session_id: str | None = None
        self._last_seq: int | None = None
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None
        self._receive_task: asyncio.Task | None = None
        self._event_handlers: list[EventHandler] = []
        self._c2c_handlers: list[C2CHandler] = []
        # Dispatch queue + worker (created in start(), torn down in stop()).
        self._dispatch_queue: asyncio.Queue | None = None
        self._dispatch_task: asyncio.Task | None = None

    # M5: expose TokenManager as a public read-only property so callers don't
    #     have to reach into a private attribute.
    @property
    def token_manager(self) -> TokenManager:
        return self._token_mgr

    @property
    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        try:
            # websockets >= 16 uses `open`; older versions use `state`
            if hasattr(self._ws, "open"):
                return bool(self._ws.open)
            return self._ws.state == websockets.protocol.State.OPEN  # type: ignore[attr-defined]
        except Exception:
            return False

    def on_group_at_message(self, handler: EventHandler) -> None:
        """Register a handler for GROUP_AT_MESSAGE_CREATE events."""
        self._event_handlers.append(handler)

    def on_c2c_message(self, handler: C2CHandler) -> None:
        """Register a handler for C2C_MSG_RECEIVE events (private chat)."""
        self._c2c_handlers.append(handler)

    async def start(self) -> None:
        """Start the client and maintain the connection."""
        self._running = True
        settings = get_settings()

        if not settings.qq_enabled:
            logger.warning("QQ Bot not configured (missing AppID/AppSecret), skipping")
            return

        # Launch the dispatch worker once — it lives for the entire client
        # lifetime, *across* reconnections, so events already received but
        # not yet processed are not lost when the socket drops.
        self._dispatch_queue = asyncio.Queue()
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())

        logger.info("Starting QQ Bot client...")
        while self._running:
            try:
                await self._connect_and_run()
            except websockets.ConnectionClosed as e:
                logger.warning("WebSocket closed: %s, reconnecting in 5s...", e)
            except Exception:
                logger.exception("Unexpected error, reconnecting in 5s...")
            await asyncio.sleep(5)

        # Shut down the dispatch worker.
        if self._dispatch_queue is not None:
            await self._dispatch_queue.put(_SHUTDOWN)
        if self._dispatch_task is not None:
            try:
                await self._dispatch_task
            except Exception:
                logger.exception("Dispatch worker exited with error")
        logger.info("QQ Bot client stopped")

    async def stop(self) -> None:
        """Gracefully stop the client."""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._receive_task:
            self._receive_task.cancel()
        if self._ws:
            await self._ws.close()
        # Signal the dispatch worker to drain and exit.
        if self._dispatch_queue is not None:
            await self._dispatch_queue.put(_SHUTDOWN)
        if self._dispatch_task is not None:
            try:
                await asyncio.wait_for(self._dispatch_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._dispatch_task.cancel()
            except Exception:
                logger.exception("Error stopping dispatch worker")
        logger.info("QQ Bot client stop requested")

    @staticmethod
    def _create_ssl_context() -> ssl.SSLContext:
        """Create an SSL context that trusts system root certificates."""
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
        return ctx

    @staticmethod
    def _enable_tcp_keepalive(ws) -> None:
        """Enable TCP keepalive on the underlying socket to survive NAT timeouts."""
        try:
            transport = getattr(ws, "transport", None)
            if transport is None:
                return
            sock = transport.get_extra_info("socket")
            if sock is None:
                return
            import socket as sock_mod
            sock.setsockopt(sock_mod.SOL_SOCKET, sock_mod.SO_KEEPALIVE, 1)
            # macOS/BSD: idle seconds before keepalive probes start
            try:
                sock.setsockopt(sock_mod.IPPROTO_TCP, sock_mod.TCP_KEEPALIVE, 30)
            except (OSError, AttributeError):
                pass
            logger.debug("TCP keepalive enabled")
        except Exception:
            logger.debug("Could not enable TCP keepalive")

    async def _connect_and_run(self) -> None:
        """Establish connection and run the event loop."""
        gateway_url = await get_gateway_url(self._token_mgr)
        logger.info("Connecting to gateway: %s", gateway_url)

        ssl_ctx = self._create_ssl_context()
        # Disable websockets' built-in ping — QQ uses app-level OpCode 1 heartbeat
        async with websockets.connect(
            gateway_url,
            ssl=ssl_ctx,
            ping_interval=None,
            ping_timeout=None,
        ) as ws:
            self._ws = ws

            # Wait for Hello
            hello = await ws.recv()
            hello_data = json.loads(hello)
            if hello_data.get("op") != OP_HELLO:
                raise RuntimeError(f"Expected Hello, got: {hello_data}")

            self._heartbeat_interval = hello_data["d"]["heartbeat_interval"] / 1000.0
            logger.info("Hello received, heartbeat interval: %.1fs", self._heartbeat_interval)

            # Authenticate or Resume
            if self._session_id and self._last_seq is not None:
                await self._send_resume()
            else:
                await self._send_identify()

            # Wait for Ready (skip any events that arrive before it)
            for _ in range(10):
                raw = await ws.recv()
                data = json.loads(raw)
                if data.get("t") == "READY":
                    self._session_id = data["d"]["session_id"]
                    logger.info("Ready! session_id=%s", self._session_id)
                    break
                if data.get("op") == OP_RECONNECT:  # Reconnect — server requests immediate reconnect
                    logger.warning("Got OpCode 7 Reconnect during handshake")
                    raise websockets.ConnectionClosed(None, "server requested reconnect")
                if data.get("op") == OP_INVALID_SESSION:  # Invalid Session
                    raise RuntimeError(f"Identify rejected: {data}")
                logger.warning(
                    "WSpreready skip #%d t=%s (is event arriving before READY)",
                    _ + 1,
                    data.get("t", f"op={data.get('op')}"),
                )
            else:
                raise RuntimeError("Did not receive READY after 10 messages")

            # Enable TCP keepalive to survive NAT/firewall timeouts (~5 min)
            self._enable_tcp_keepalive(ws)

            # Start heartbeat and receive loop; wait for either to finish.
            # The dispatch worker is started once in start() and intentionally
            # NOT awaited here — it must keep running across reconnections.
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._receive_task = asyncio.create_task(self._receive_loop())
            done, pending = await asyncio.wait(
                [self._heartbeat_task, self._receive_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                # Await cancellation to suppress "Task was destroyed but it is pending"
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _send_identify(self) -> None:
        """Send OpCode 2 Identify to authenticate."""
        token = await self._token_mgr.get_token()
        payload = {
            "op": OP_IDENTIFY,
            "d": {
                "token": f"QQBot {token}",
                "intents": GROUP_AND_C2C,
                "shard": [0, 1],
                "properties": {
                    "$os": "linux",
                    "$browser": "qq-ai-bot",
                    "$device": "qq-ai-bot",
                },
            },
        }
        await self._ws.send(json.dumps(payload))  # type: ignore[union-attr]
        logger.info("Identify sent")

    async def _send_resume(self) -> None:
        """Send OpCode 6 Resume to restore session after reconnect."""
        token = await self._token_mgr.get_token()
        payload = {
            "op": OP_RESUME,
            "d": {
                "token": f"QQBot {token}",
                "session_id": self._session_id,
                "seq": self._last_seq,
            },
        }
        await self._ws.send(json.dumps(payload))  # type: ignore[union-attr]
        logger.info("Resume sent (session_id=%s, seq=%s)", self._session_id, self._last_seq)

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats. Log at WARNING to always be visible."""
        while self._running:
            await asyncio.sleep(self._heartbeat_interval * 0.8)
            if not (self._running and self.is_connected):
                break
            try:
                payload = {
                    "op": OP_HEARTBEAT,
                    "d": self._last_seq,
                }
                await asyncio.wait_for(
                    self._ws.send(json.dumps(payload)),  # type: ignore[union-attr]
                    timeout=5.0,
                )
                logger.debug("💓 hb seq=%s", self._last_seq)
            except Exception:
                logger.warning("💓 hb failed")
                break

    async def _receive_loop(self) -> None:
        """Receive WebSocket frames and enqueue dispatch events.

        This loop must **never** block on handler execution.  DISPATCH
        events are pushed onto ``self._dispatch_queue`` for the separate
        dispatch worker to process.  Control opcodes (heartbeat ACK,
        server-requested reconnect, invalid session) are handled inline
        so the connection lifecycle is responsive even while a long
        handler is running in the worker.
        """
        while self._running and self.is_connected:
            try:
                raw = await self._ws.recv()  # type: ignore[union-attr]
            except Exception:
                break
            data = json.loads(raw)
            op = data.get("op")

            # Track sequence number for resume
            if data.get("s") is not None:
                self._last_seq = data["s"]

            if op == OP_DISPATCH:
                # H2: keep dispatch metadata visible at WARN but move full payload
                #     (which contains user message content) to DEBUG to avoid
                #     spilling private chat content into log files.
                logger.warning(
                    "WSrecv DISPATCH t=%s s=%s",
                    data.get("t", ""),
                    data.get("s"),
                )
                logger.debug(
                    "WSrecv DISPATCH payload=%s",
                    json.dumps(data.get("d", {}), ensure_ascii=False),
                )
                # Enqueue for the dispatch worker — do NOT await handler here.
                if self._dispatch_queue is not None:
                    await self._dispatch_queue.put(data)
            elif op == OP_HEARTBEAT_ACK:
                logger.debug("Heartbeat ACK received")
            elif op == OP_RECONNECT:
                # Server requests an immediate reconnect — break out so
                # _connect_and_run returns and start() reconnects.
                logger.warning("Got OpCode 7 Reconnect from server, reconnecting...")
                break
            elif op == OP_INVALID_SESSION:
                # Session is no longer valid — clear resume state so the
                # next connect does a fresh Identify instead of Resume.
                logger.warning("Got OpCode 9 Invalid Session, will re-identify")
                self._session_id = None
                self._last_seq = None
                break
            else:
                logger.warning("WSrecv op=%s data=%s", op, json.dumps(data, ensure_ascii=False)[:300])

    async def _dispatch_loop(self) -> None:
        """Process queued dispatch events.

        Runs for the entire client lifetime (started in ``start()``,
        stopped in ``stop()``).  Events are processed one at a time to
        avoid race conditions in conversation-session management.
        """
        assert self._dispatch_queue is not None
        while True:
            data = await self._dispatch_queue.get()
            if data is _SHUTDOWN:
                break
            try:
                await self._handle_dispatch(data)
            except Exception:
                logger.exception("Error in dispatch loop")

    async def _handle_dispatch(self, data: dict) -> None:
        """Route dispatch events to registered handlers."""
        event_type = data.get("t", "")
        payload = data.get("d", {})

        # H2: log only metadata + a short content preview; full payload stays in DEBUG.
        logger.warning(
            "WSdispatch event=%s event_id=%s group=%s author=%s",
            event_type,
            (payload.get("id", "") or "")[:30],
            (payload.get("group_openid", "") or "")[:30],
            (
                payload.get("author", {}).get("member_openid")
                or payload.get("author", {}).get("user_openid")
                or ""
            )[:30],
        )
        logger.debug(
            "WSdispatch content=%r",
            (payload.get("content") or "")[:120],
        )

        if event_type == "GROUP_AT_MESSAGE_CREATE":
            event = GroupAtMessageEvent(**payload)
            for handler in self._event_handlers:
                try:
                    await handler(event)
                except Exception:
                    logger.exception("Error in event handler")
        elif event_type == "C2C_MESSAGE_CREATE":
            event = C2CMessageEvent(**payload)
            for handler in self._c2c_handlers:
                try:
                    await handler(event)
                except Exception:
                    logger.exception("Error in C2C event handler")
        elif event_type == "RESUMED":
            logger.info("Session resumed successfully")
        else:
            logger.warning("WSdispatch UNHANDLED event_type=%s payload_keys=%s", event_type, list(payload.keys()))
