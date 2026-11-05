# -*- coding: utf-8 -*-
"""文档检索编排：子块召回、父块融合、正文回查和可选精排。"""

from services.retrieval.rag.fusion.backfill.service import ParentBackfiller
from services.retrieval.rag.fusion.models import DEFAULT_MATCHED_CHILDREN_LIMIT
from services.retrieval.rag.fusion.models import FusionInput
from services.retrieval.rag.fusion.service import RRFFuser
from services.retrieval.rag.keyword.models import KeywordRetrievalInput
from services.retrieval.rag.keyword.models import KeywordSearchResult
from services.retrieval.rag.keyword.service import KeywordRetriever
from services.retrieval.rag.models import RagSearchRequest
from services.retrieval.rag.models import RagSearchResult
from services.retrieval.rag.rerank.models import RerankInput
from services.retrieval.rag.rerank.service import Reranker
from services.retrieval.rag.vector.models import VectorRetrievalInput
from services.retrieval.rag.vector.models import VectorSearchResult
from services.retrieval.rag.vector.service import VectorRetriever


class RagRetriever:
    """只处理非结构化文档。不判断该不该查表。"""

    def __init__(
        self,
        vector: VectorRetriever,
        keyword: KeywordRetriever,
        fuser: RRFFuser,
        backfiller: ParentBackfiller,
        reranker: Reranker | None = None,
    ) -> None:
        self._vector = vector
        self._keyword = keyword
        self._fuser = fuser
        self._backfiller = backfiller
        self._reranker = reranker

    def search(self, request: RagSearchRequest) -> RagSearchResult:
        """按父块召回文档。精确匹配场景只走关键词，其余先向量再关键词。"""
        if not request.documents:
            return RagSearchResult()

        versions = {
            document.id: document.parse_version for document in request.documents
        }
        document_ids = tuple(versions)
        vector_result = VectorSearchResult()
        if not request.keyword_only:
            vector_result = self._vector.retrieve(
                VectorRetrievalInput(
                    query=request.query,
                    document_ids=document_ids,
                    file_types=request.file_types,
                    document_versions=versions,
                    request_id=request.request_id,
                )
            )
        keyword_result = self._keyword.retrieve(
            KeywordRetrievalInput(
                query=request.query,
                document_ids=document_ids,
                file_types=request.file_types,
                page_range=request.page_range,
                document_versions=versions,
                extracted_keywords=request.extracted_keywords,
                include_highlight=request.include_highlight,
                request_id=request.request_id,
            )
        )
        fused = self._fuser.fuse(
            FusionInput(
                vector_hits=vector_result.hits,
                keyword_hits=keyword_result.hits,
                top_k=request.top_k,
                matched_children_limit=(
                    DEFAULT_MATCHED_CHILDREN_LIMIT if request.include_children else 0
                ),
                request_id=request.request_id,
            )
        )
        filled = self._backfiller.backfill(
            fused.candidates,
            allowed_versions=versions,
        )
        parents = filled.parents
        reranked = False
        rerank_error = None
        if self._reranker is not None:
            rerank_result = self._reranker.rerank(
                RerankInput(
                    query=request.query,
                    candidates=filled.parents,
                    top_n=request.top_k,
                    request_id=request.request_id,
                    requested=request.rerank,
                )
            )
            parents = rerank_result.ranked
            reranked = rerank_result.reranked
            rerank_error = rerank_result.rerank_error
        reasons = _degraded_reasons(vector_result, keyword_result)
        return RagSearchResult(
            parents=parents,
            degraded=bool(reasons),
            degraded_reasons=reasons,
            reranked=reranked,
            rerank_error=rerank_error,
            missing_parent_count=filled.missing_parent_count,
        )


def _degraded_reasons(
    vector_result: VectorSearchResult,
    keyword_result: KeywordSearchResult,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if vector_result.degraded and vector_result.degraded_reason:
        reasons.append(vector_result.degraded_reason)
    if keyword_result.degraded and keyword_result.degraded_reason:
        reasons.append(keyword_result.degraded_reason)
    return tuple(reasons)
