# -*- coding: utf-8 -*-
"""文档检索编排的输入和输出。"""

from dataclasses import dataclass

from model.document.model import Document
from services.retrieval.filters import PageRange
from services.retrieval.rag.fusion.backfill.models import FusedParent


@dataclass(frozen=True)
class RagSearchRequest:
    """已经完成改写和路由的文档检索请求。"""

    query: str
    documents: tuple[Document, ...]
    top_k: int = 10
    file_types: tuple[str, ...] | None = None
    page_range: PageRange | None = None
    keyword_only: bool = False
    extracted_keywords: tuple[str, ...] = ()
    rerank: bool = False
    include_children: bool = True
    include_highlight: bool = True
    request_id: str = ""


@dataclass(frozen=True)
class RagSearchResult:
    """父块列表，以及两路召回和精排的降级信息。"""

    parents: tuple[FusedParent, ...] = ()
    degraded: bool = False
    degraded_reasons: tuple[str, ...] = ()
    reranked: bool = False
    rerank_error: str | None = None
    missing_parent_count: int = 0
