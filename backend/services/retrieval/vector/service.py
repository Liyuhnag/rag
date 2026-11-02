# -*- coding: utf-8 -*-
"""Milvus 子块向量检索服务（VectorRetriever）。

负责：
1. 调用 DashScopeEmbedder 获取查询向量（失败时标记 EMBEDDING_UNAVAILABLE 降级）；
2. 构造安全转义的 Milvus 布尔过滤表达式（document_ids、file_types、version）；
3. 执行 HNSW COSINE 检索，对同分子块按 (parent_index, child_order) 稳定排序并赋予 rank(1..N)；
4. Milvus 故障或超时时重试 1 次，仍失败则标记 VECTOR_UNAVAILABLE 降级返回，不阻断 ES 检索。
"""

import logging
import time
from collections.abc import Sequence

from config import Settings
from services.indexing.vector_index.embedding import DashScopeEmbedder
from services.retrieval.vector.models import DEFAULT_SEARCH_EF
from services.retrieval.vector.models import DEFAULT_VECTOR_TOP_K
from services.retrieval.vector.models import VectorHit
from services.retrieval.vector.models import VectorRetrievalInput
from services.retrieval.vector.models import VectorSearchResult

logger = logging.getLogger("rag.retrieval.vector")

_OUTPUT_FIELDS = [
    "chunk_id",
    "parent_id",
    "parent_index",
    "child_order",
    "document_id",
    "version",
    "chunk_index",
    "file_type",
]
_MAX_DOC_IDS_FILTER = 1000
_MAX_FILE_TYPES_FILTER = 20


def escape_milvus_string(value: str) -> str:
    """转义 Milvus 过滤表达式中的双引号与反斜杠，防止表达式注入。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_milvus_filter(
    document_ids: Sequence[str],
    file_types: Sequence[str] | None = None,
    document_versions: dict[str, int] | None = None,
) -> str:
    """构造下推到 Milvus 的布尔过滤表达式。"""
    clean_docs = [doc_id.strip() for doc_id in document_ids if doc_id.strip()][
        :_MAX_DOC_IDS_FILTER
    ]
    if not clean_docs:
        return ""

    clauses: list[str] = []
    if document_versions:
        ver_pairs = []
        for doc_id in clean_docs:
            escaped = escape_milvus_string(doc_id)
            ver = document_versions.get(doc_id)
            if ver is not None:
                ver_pairs.append(
                    f'(document_id == "{escaped}" and version == {int(ver)})'
                )
            else:
                ver_pairs.append(f'document_id == "{escaped}"')
        clauses.append(
            ver_pairs[0] if len(ver_pairs) == 1 else f"({' or '.join(ver_pairs)})"
        )
    else:
        literals = ", ".join(f'"{escape_milvus_string(d)}"' for d in clean_docs)
        clauses.append(f"document_id in [{literals}]")

    if file_types:
        clean_types = [
            ft.lower().strip() for ft in file_types if ft and ft.strip()
        ][:_MAX_FILE_TYPES_FILTER]
        if clean_types:
            ft_literals = ", ".join(
                f'"{escape_milvus_string(ft)}"' for ft in clean_types
            )
            clauses.append(f"file_type in [{ft_literals}]")

    return " and ".join(clauses)


class VectorRetriever:
    """编排查询向量化与 Milvus HNSW 检索。"""

    def __init__(
        self,
        settings: Settings,
        embedder: DashScopeEmbedder | None = None,
        client=None,
        *,
        search_ef: int = DEFAULT_SEARCH_EF,
        timeout_seconds: float = 3.0,
        sleep=time.sleep,
    ) -> None:
        self.settings = settings
        self.embedder = embedder or DashScopeEmbedder(settings)
        self._client = client
        self.search_ef = max(16, search_ef)
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep

    def _get_client(self):
        if self._client is not None:
            return self._client
        from pymilvus import MilvusClient

        return MilvusClient(
            uri=self.settings.milvus_uri,
            token=self.settings.milvus_token,
            db_name=self.settings.milvus_database,
            timeout=self.timeout_seconds,
        )

    def retrieve(self, request: VectorRetrievalInput) -> VectorSearchResult:
        """执行向量召回；单路异常时返回带 degraded 标记的空结果。"""
        query = request.query.strip()
        if not query or not request.document_ids:
            return VectorSearchResult()
        if request.file_types is not None and len(request.file_types) == 0:
            return VectorSearchResult()

        top_k = max(1, min(request.top_k, 200)) if request.top_k else DEFAULT_VECTOR_TOP_K

        # 1. 查询向量化
        embed_started = time.monotonic()
        try:
            vectors = self.embedder.embed([query])
            if not vectors or len(vectors[0]) != self.settings.embedding_dim:
                raise RuntimeError("查询向量维度与配置不一致")
            query_vector = list(vectors[0])
        except Exception as exc:
            logger.warning("查询向量化失败，降级为关键词检索: %s", exc)
            return VectorSearchResult(
                embedding_ms=max(0, int((time.monotonic() - embed_started) * 1000)),
                degraded=True,
                degraded_reason="EMBEDDING_UNAVAILABLE",
                error_message=str(exc),
            )
        embedding_ms = max(0, int((time.monotonic() - embed_started) * 1000))

        # 2. 构造过滤表达式
        filter_expr = build_milvus_filter(
            request.document_ids,
            request.file_types,
            request.document_versions,
        )
        if not filter_expr:
            return VectorSearchResult(embedding_ms=embedding_ms)

        # 3. 查询 Milvus（失败时重试 1 次）
        started = time.monotonic()
        raw_hits = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                raw_hits = self._search_milvus(query_vector, filter_expr, top_k)
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Milvus 向量检索异常 attempt=%d: %s", attempt + 1, exc
                )
                if attempt == 0:
                    self.sleep(0.1)

        vector_ms = max(0, int((time.monotonic() - started) * 1000))
        if raw_hits is None:
            return VectorSearchResult(
                embedding_ms=embedding_ms,
                vector_ms=vector_ms,
                degraded=True,
                degraded_reason="VECTOR_UNAVAILABLE",
                error_message=str(last_error) if last_error else "Milvus 不可用",
            )

        hits = _parse_milvus_hits(raw_hits, top_k)
        return VectorSearchResult(
            hits=hits,
            embedding_ms=embedding_ms,
            vector_ms=vector_ms,
        )

    def _search_milvus(
        self,
        query_vector: list[float],
        filter_expr: str,
        limit: int,
    ) -> list[dict]:
        client = self._get_client()
        owns_client = self._client is None
        try:
            response = client.search(
                collection_name=self.settings.milvus_collection,
                data=[query_vector],
                anns_field="vector",
                search_params={
                    "metric_type": "COSINE",
                    "params": {"ef": max(self.search_ef, limit)},
                },
                limit=limit,
                filter=filter_expr,
                output_fields=_OUTPUT_FIELDS,
                timeout=self.timeout_seconds,
            )
        finally:
            if owns_client and hasattr(client, "close"):
                client.close()

        if not response:
            return []
        first_batch = response[0]
        return list(first_batch) if isinstance(first_batch, Sequence) else []


def _parse_milvus_hits(raw_hits: Sequence[dict], limit: int) -> tuple[VectorHit, ...]:
    """解析 Milvus 命中字典，按 (-score, parent_index, child_order, chunk_id) 稳定排序并去重。"""
    candidates: list[tuple[float, int, int, str, dict]] = []
    seen_chunks: set[str] = set()

    for item in raw_hits:
        entity = item.get("entity") if isinstance(item.get("entity"), dict) else item
        chunk_id = str(entity.get("chunk_id") or item.get("id") or "").strip()
        parent_id = str(entity.get("parent_id") or "").strip()
        document_id = str(entity.get("document_id") or "").strip()
        if not chunk_id or not parent_id or not document_id or chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)

        raw_score = item.get("distance", item.get("score", 0.0))
        score = float(raw_score)
        parent_index = int(entity.get("parent_index", 0))
        child_order = int(entity.get("child_order", 0))
        candidates.append((score, parent_index, child_order, chunk_id, entity))

    candidates.sort(key=lambda row: (-row[0], row[1], row[2], row[3]))

    results: list[VectorHit] = []
    for rank, (score, parent_index, child_order, chunk_id, entity) in enumerate(
        candidates[:limit], start=1
    ):
        results.append(
            VectorHit(
                chunk_id=chunk_id,
                parent_id=str(entity["parent_id"]),
                parent_index=parent_index,
                child_order=child_order,
                document_id=str(entity["document_id"]),
                version=int(entity.get("version", 1)),
                chunk_index=int(entity.get("chunk_index", 0)),
                file_type=str(entity.get("file_type", "")),
                score=score,
                rank=rank,
            )
        )
    return tuple(results)
