"""Private chat (C2C) handler: receives QQ DM events, calls AI, sends replies."""

import logging

from qq_bot.events import C2CMessageEvent
from qq_bot.messages import MessageSender
from services.ai_router import AiRouter

logger = logging.getLogger(__name__)


class C2CAiHandler:
    """Handles C2C_MSG_RECEIVE events (private/direct messages)."""

    def __init__(self, message_sender: MessageSender, ai_router: AiRouter) -> None:
        self._sender = message_sender
        self._ai = ai_router

    async def handle(self, event: C2CMessageEvent) -> None:
        """Process a C2C message event."""
        content = event.content.strip()
        if not content:
            return

        user_openid = event.author.user_openid
        msg_id = event.id

        logger.info("DM from %s: %s", user_openid, content[:100])

        try:
            reply = await self._ai.chat([{"role": "user", "content": content}])
            if not reply or not reply.strip():
                reply = "嗯... 让我想想哦～ 稍等一下嘛 😊"
            await self._sender.send_c2c_message(
                user_openid=user_openid,
                content=reply,
                msg_id=msg_id,
            )
        except Exception:
            logger.exception("Error processing DM")
            await self._sender.send_c2c_message(
                user_openid=user_openid,
                content="抱歉，AI 服务暂时不可用，请稍后再试。",
                msg_id=msg_id,
            )
