from typing import Any

from pydantic import BaseModel


class SidekickMessage(BaseModel):
    role: str
    content: str


class SidekickChatPayload(BaseModel):
    resource_type: str
    handle: str
    messages: list[SidekickMessage]
    client_draft: dict[str, str] | None = None


class SidekickChatResult(BaseModel):
    reply: str
    field_updates: dict[str, Any] = {}
