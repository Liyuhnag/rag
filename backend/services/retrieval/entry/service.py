# -*- coding: utf-8 -*-
"""检索入口：先改写，再路由，然后把请求分发给文档检索或结构化查询。"""

from services.retrieval.entry.models import RetrievalRequest
from services.retrieval.entry.models import RetrievalResult
from services.retrieval.entry.rewrite.models import RewriteRequest
from services.retrieval.entry.rewrite.service import QueryRewriter
from services.retrieval.entry.router.models import RouteRequest
from services.retrieval.entry.router.models import RouteType
from services.retrieval.entry.router.models import SearchMode
from services.retrieval.entry.router.service import QueryRouter
from services.retrieval.rag.models import RagSearchRequest
from services.retrieval.rag.service import RagRetriever
from services.retrieval.structured.models import StructuredQueryRequest
from services.retrieval.structured.service import StructuredQueryService


class RetrievalEntry:
    """一次查询只经过这里。文档检索和结构化查询互不调用。"""

    def __init__(
        self,
        rewriter: QueryRewriter,
        router: QueryRouter,
        rag: RagRetriever,
        structured: StructuredQueryService,
    ) -> None:
        self._rewriter = rewriter
        self._router = router
        self._rag = rag
        self._structured = structured

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """改写问题，判断链路，并把改写后的查询交给对应检索器。"""
        rewritten = self._rewriter.rewrite(
            RewriteRequest(
                question=request.question,
                conversation_history=request.conversation_history,
                conversation_summary=request.conversation_summary,
                default_file_types=request.file_types,
                request_id=request.request_id,
            )
        )
        decision = self._router.route(
            RouteRequest(
                query=rewritten.rewritten_query,
                top_k=request.top_k,
                document_ids=request.document_ids,
                file_types=rewritten.file_types,
                page_range=request.page_range,
                rerank=request.rerank,
                include_children=request.include_children,
                include_highlight=request.include_highlight,
                request_id=request.request_id,
            )
        )
        if decision.route_type == RouteType.EMPTY:
            return RetrievalResult(
                route_type=RouteType.EMPTY,
                rewritten_query=decision.normalized_query,
                reason=decision.reason,
            )
        if decision.route_type == RouteType.SQL:
            table_result = self._structured.query(
                StructuredQueryRequest(
                    query=decision.normalized_query,
                    datasets=decision.matched_datasets,
                    request_id=request.request_id,
                )
            )
            return RetrievalResult(
                route_type=RouteType.SQL,
                rewritten_query=decision.normalized_query,
                reason=decision.reason,
                dataset_ids=table_result.dataset_ids,
                structured_ready=table_result.ready,
                message=table_result.message,
            )

        document_result = self._rag.search(
            RagSearchRequest(
                query=decision.normalized_query,
                documents=decision.ready_documents,
                top_k=request.top_k,
                file_types=rewritten.file_types,
                page_range=request.page_range,
                keyword_only=decision.search_mode == SearchMode.KEYWORD_FIRST,
                extracted_keywords=decision.extracted_keywords,
                rerank=request.rerank,
                include_children=request.include_children,
                include_highlight=request.include_highlight,
                request_id=request.request_id,
            )
        )
        return RetrievalResult(
            route_type=RouteType.NL,
            rewritten_query=decision.normalized_query,
            reason=decision.reason,
            parents=document_result.parents,
            degraded=document_result.degraded,
            degraded_reasons=document_result.degraded_reasons,
            reranked=document_result.reranked,
            rerank_error=document_result.rerank_error,
            missing_parent_count=document_result.missing_parent_count,
        )
