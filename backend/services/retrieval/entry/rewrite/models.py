# -*- coding: utf-8 -*-
"""查询理解与改写阶段的数据模型。"""

from dataclasses import dataclass
from typing import Literal

DEFAULT_MAX_SUB_QUERIES = 3
DEFAULT_MIN_CONFIDENCE = 0.6


@dataclass(frozen=True)
class ChatMessage:
    """单条对话历史消息。"""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class RewriteRequest:
    """查询改写输入参数。"""

    question: str
    conversation_history: tuple[ChatMessage, ...] = ()
    conversation_summary: str | None = None
    default_file_types: tuple[str, ...] | None = None
    request_id: str = ""


@dataclass(frozen=True)
class RewriteResult:
    """查询理解与改写输出结果。"""

    original_question: str
    rewritten_query: str
    sub_queries: tuple[str, ...] = ()
    file_types: tuple[str, ...] | None = None
    keywords: tuple[str, ...] = ()
    is_follow_up: bool = False
    confidence: float = 1.0
    method: Literal["llm", "skipped", "fallback"] = "skipped"
    latency_ms: int = 0
