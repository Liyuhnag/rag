# -*- coding: utf-8 -*-
"""父块聚合与父块级 RRF 融合阶段的数据模型（不含 MySQL 父块正文回查）。"""

from dataclasses import dataclass

from services.retrieval.rag.keyword.models import KeywordHit
from services.retrieval.rag.vector.models import VectorHit

DEFAULT_RRF_K = 60
DEFAULT_FUSION_TOP_K = 10
DEFAULT_MATCHED_CHILDREN_LIMIT = 3
DEFAULT_CHILD_CONTENT_MAX_CHARS = 300


@dataclass(frozen=True)
class MatchedChild:
    """父块下命中的子块片段及两路排名/分数信息。"""

    chunk_id: str
    child_order: int
    content: str
    highlight: str | None
    vector_rank: int | None
    keyword_rank: int | None
    vector_score: float | None
    keyword_score: float | None


@dataclass(frozen=True)
class FusedParentCandidate:
    """经父块级 RRF 融合排序后的父块候选（待后续多级缓存/MySQL 回查填充父块正文）。"""

    parent_id: str
    document_id: str
    version: int
    parent_index: int
    file_type: str
    heading_path: str
    locator: str
    page_start: int | None
    page_end: int | None
    rrf_score: float
    rank: int
    vector_parent_rank: int | None
    keyword_parent_rank: int | None
    matched_child_count: int
    matched_children: tuple[MatchedChild, ...]


@dataclass(frozen=True)
class FusionInput:
    """父块级 RRF 融合输入参数。"""

    vector_hits: tuple[VectorHit, ...] = ()
    keyword_hits: tuple[KeywordHit, ...] = ()
    top_k: int = DEFAULT_FUSION_TOP_K
    rrf_k: int = DEFAULT_RRF_K
    matched_children_limit: int = DEFAULT_MATCHED_CHILDREN_LIMIT
    child_content_max_chars: int = DEFAULT_CHILD_CONTENT_MAX_CHARS
    request_id: str = ""


@dataclass(frozen=True)
class FusionResult:
    """父块级 RRF 融合输出结果。"""

    candidates: tuple[FusedParentCandidate, ...] = ()
    total_parent_candidates: int = 0
    fusion_ms: int = 0

    @property
    def parent_ids(self) -> tuple[str, ...]:
        """按 RRF 最终排名返回待回查的 parent_id 元组。"""
        return tuple(item.parent_id for item in self.candidates)
