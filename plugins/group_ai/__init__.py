"""Group chat @bot handler: receives QQ events, calls AI, sends replies."""

import logging
import re

from qq_bot.events import GroupAtMessageEvent
from qq_bot.messages import MessageSender
from plugins.group_ai.context import SessionManager
from services.ai_router import AiRouter

logger = logging.getLogger(__name__)


class GroupAiHandler:
    """Handles GROUP_AT_MESSAGE_CREATE events."""

    def __init__(self, message_sender: MessageSender, ai_router: AiRouter) -> None:
        self._sender = message_sender
        self._ai = ai_router
        self._sessions = SessionManager()

    async def handle(self, event: GroupAtMessageEvent) -> None:
        """Process a group @ message event."""
        # Clean content: remove @mention tags
        content = self._clean_at_mention(event.content)

        group_openid = event.group_openid
        user_openid = event.author.member_openid
        # Build @mention tag for reply using official text-chain format
        at_tag = f'<qqbot-at-user id="{user_openid}" />' if user_openid else ""
        msg_id = event.id

        # Handle commands
        cmd = content.strip().lower()
        if cmd in ("/reset", "/clear", "/new"):
            cleared = await self._sessions.clear_session(group_openid, user_openid)
            reply = "✅ 已清除对话历史，开始新对话。" if cleared else "当前没有对话记录。"
            await self._sender.send_group_message(
                group_openid=group_openid,
                content=reply,
                msg_id=msg_id,
            )
            return

        if not content.strip():
            content = "你好"

        logger.info(
            "Group @ from %s in %s: %s",
            user_openid,
            group_openid,
            content[:100],
        )

        # Get conversation history
        history = await self._sessions.get_history(group_openid, user_openid)
        history.append({"role": "user", "content": content})

        try:
            # Call AI
            reply = await self._ai.chat(history)
            if not reply or not reply.strip():
                reply = "嗯... 让我想想哦～ 稍等一下嘛 😊"

            # Prepend @mention so the sender gets notified
            # First remove any at-tags the AI may have learned from history
            reply = re.sub(r"<qqbot-at-user[^>]*/\s*>", "", reply).strip()
            if at_tag:
                reply = f"{at_tag} {reply}"

            # Update session (without the at_tag to keep history clean)
            history.append({"role": "assistant", "content": reply})
            await self._sessions.trim_history(group_openid, user_openid)

            # Send reply (passive, with msg_id)
            await self._sender.send_group_message(
                group_openid=group_openid,
                content=reply,
                msg_id=msg_id,
            )
        except Exception:
            logger.exception("Error processing group @ message")
            try:
                await self._sender.send_group_message(
                    group_openid=group_openid,
                    content="抱歉，AI 服务暂时不可用，请稍后再试。",
                    msg_id=msg_id,
                )
            except Exception:
                # M7: if even the error notification fails, don't mask the
                #     original AI failure — log and move on.
                logger.exception("Failed to deliver error notification")

    @staticmethod
    def _clean_at_mention(content: str) -> str:
        """Remove @mention markup from message content.

        QQ official format: @{$username} or similar markup.
        """
        # Remove patterns like @username or <@userid>
        cleaned = re.sub(r"@\{\$[^}]+\}", "", content)
        cleaned = re.sub(r"<@[^>]+>", "", cleaned)
        return cleaned.strip()
