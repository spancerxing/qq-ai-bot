"""Event models for QQ Bot WebSocket events."""

from pydantic import BaseModel, Field


class AuthorMember(BaseModel):
    member_openid: str = ""
    member_role: str = "member"
    bot: bool = False


class AuthorUser(BaseModel):
    """Author for C2C (private) messages."""

    user_openid: str = ""


class Event(BaseModel):
    """Base event model."""

    op: int = 0
    s: int | None = None
    t: str = ""


class GroupAtMessageEvent(BaseModel):
    """GROUP_AT_MESSAGE_CREATE event payload."""

    id: str = ""
    content: str = ""
    timestamp: str = ""
    group_openid: str = ""
    author: AuthorMember = Field(default_factory=AuthorMember)


class C2CMessageEvent(BaseModel):
    """C2C_MSG_RECEIVE event payload (private/direct messages)."""

    id: str = ""
    content: str = ""
    timestamp: str = ""
    author: AuthorUser = Field(default_factory=AuthorUser)
