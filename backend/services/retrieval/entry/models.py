# -*- coding: utf-8 -*-
"""一次检索请求的入口参数和分发结果。"""

from dataclasses import dataclass

from services.retrieval.entry.rewrite.models import ChatMessage
from services.retrieval.entry.router.models import RouteType
from services.retrieval.filters import PageRange
from services.retrieval.rag.fusion.backfill.models import FusedParent

DEFAULT_TOP_K = 10


@dataclass(frozen=True)
class RetrievalRequest:
    """调用方进入检索入口的原始问题。"""

    question: str
    top_k: int = DEFAULT_TOP_K
    document_ids: tuple[str, ...] | None = None
    file_types: tuple[str, ...] | None = None
    page_range: PageRange | None = None
    rerank: bool = False
    include_children: bool = True
    include_highlight: bool = True
    conversation_history: tuple[ChatMessage, ...] = ()
    conversation_summary: str | None = None
    request_id: str = ""


@dataclass(frozen=True)
class RetrievalResult:
    """入口分发后的统一结果。文档链路看 parents，表链路看 dataset_ids。"""

    route_type: RouteType
    rewritten_query: str
    reason: str = ""
    parents: tuple[FusedParent, ...] = ()
    dataset_ids: tuple[str, ...] = ()
    structured_ready: bool = False
    message: str = ""
    degraded: bool = False
    degraded_reasons: tuple[str, ...] = ()
    reranked: bool = False
    rerank_error: str | None = None
    missing_parent_count: int = 0
