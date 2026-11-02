# -*- coding: utf-8 -*-
"""Elasticsearch 关键词检索阶段的数据模型。"""

from dataclasses import dataclass

from services.retrieval.filters import PageRange

DEFAULT_KEYWORD_TOP_K = 50


@dataclass(frozen=True)
class KeywordRetrievalInput:
    """Elasticsearch 子块 BM25 检索输入参数。"""

    query: str
    document_ids: tuple[str, ...]
    top_k: int = DEFAULT_KEYWORD_TOP_K
    file_types: tuple[str, ...] | None = None
    page_range: PageRange | None = None
    document_versions: dict[str, int] | None = None
    extracted_keywords: tuple[str, ...] = ()
    include_highlight: bool = True
    request_id: str = ""


@dataclass(frozen=True)
class KeywordHit:
    """Elasticsearch 返回的单条子块关键词命中。"""

    chunk_id: str
    parent_id: str
    parent_index: int
    child_order: int
    document_id: str
    version: int
    chunk_index: int
    file_type: str
    content: str
    heading_path: str
    locator: str
    page_start: int | None
    page_end: int | None
    score: float
    highlight: str | None
    rank: int


@dataclass(frozen=True)
class KeywordSearchResult:
    """关键词检索阶段输出（含耗时与降级状态）。"""

    hits: tuple[KeywordHit, ...] = ()
    keyword_ms: int = 0
    degraded: bool = False
    degraded_reason: str | None = None
    error_message: str | None = None
