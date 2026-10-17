# -*- coding: utf-8 -*-
"""对话服务占位。检索和模型生成由后续实现补上。"""

from models.chat import ChatResponse
from services.exceptions import FeatureNotReady


class ChatService:
    """对话服务占位。"""

    def answer(
        self,
        question: str,
        conversation_id: str | None,
    ) -> ChatResponse:
        del question, conversation_id
        raise FeatureNotReady("检索生成尚未接入")
