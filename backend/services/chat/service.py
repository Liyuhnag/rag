# -*- coding: utf-8 -*-
"""对话服务占位。检索和模型生成由后续实现补上。"""

from typing import NoReturn

from services.exceptions import FeatureNotReady


class ChatService:
    """对话服务占位。"""

    def answer(
        self,
        question: str,
        conversation_id: str | None,
    ) -> NoReturn:
        del question, conversation_id
        raise FeatureNotReady("检索生成尚未接入")
