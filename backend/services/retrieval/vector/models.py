# -*- coding: utf-8 -*-
"""向量检索阶段的数据模型。"""

from dataclasses import dataclass

DEFAULT_VECTOR_TOP_K = 50
DEFAULT_SEARCH_EF = 64


@dataclass(frozen=True)
class VectorRetrievalInput:
    """Milvus 子块向量检索输入参数。"""

    query: str
    document_ids: tuple[str, ...]
    top_k: int = DEFAULT_VECTOR_TOP_K
    file_types: tuple[str, ...] | None = None
    document_versions: dict[str, int] | None = None
    request_id: str = ""


@dataclass(frozen=True)
class VectorHit:
    """Milvus 返回的单条子块向量命中。"""

    chunk_id: str
    parent_id: str
    parent_index: int
    child_order: int
    document_id: str
    version: int
    chunk_index: int
    file_type: str
    score: float
    rank: int


@dataclass(frozen=True)
class VectorSearchResult:
    """向量检索阶段输出（含耗时与降级状态）。"""

    hits: tuple[VectorHit, ...] = ()
    embedding_ms: int = 0
    vector_ms: int = 0
    degraded: bool = False
    degraded_reason: str | None = None
    error_message: str | None = None
