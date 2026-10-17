# -*- coding: utf-8 -*-
"""对话接口的请求与响应模型。"""

from datetime import datetime
from enum import Enum

from pydantic import Field
from pydantic import field_validator

from models.common import APIModel


class ChatRole(str, Enum):
    ASSISTANT = "ASSISTANT"


class SourceRef(APIModel):
    document_id: str
    name: str
    page: int | None = None


class ChatRequest(APIModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=64)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("问题不能为空")
        return stripped

    @field_validator("conversation_id")
    @classmethod
    def strip_conversation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("conversationId 不能为空")
        return stripped


class ChatResponse(APIModel):
    id: str
    conversation_id: str
    role: ChatRole
    content: str
    sources: list[SourceRef]
    created_at: datetime
