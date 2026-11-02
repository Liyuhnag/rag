# -*- coding: utf-8 -*-
"""检索阶段服务集合：查询改写、查询路由、向量召回、关键词召回、RRF 融合与 Rerank 精排。"""

from services.retrieval.keyword import KeywordHit
from services.retrieval.keyword import KeywordRetrievalInput
from services.retrieval.keyword import KeywordRetriever
from services.retrieval.keyword import KeywordSearchResult
from services.retrieval.keyword import build_es_dsl
from services.retrieval.rewrite import ChatMessage
from services.retrieval.rewrite import DashScopeRewriteClient
from services.retrieval.rewrite import LLMRewriteOutput
from services.retrieval.rewrite import QueryRewriter
from services.retrieval.rewrite import RewriteRequest
from services.retrieval.rewrite import RewriteResult
from services.retrieval.rewrite import needs_rewrite
from services.retrieval.router import DashScopeRouterClient
from services.retrieval.router import LLMRouteResult
from services.retrieval.router import PageRange
from services.retrieval.router import QueryRouter
from services.retrieval.router import RouteDecision
from services.retrieval.router import RouteRequest
from services.retrieval.router import RouteType
from services.retrieval.router import SearchMode
from services.retrieval.router import normalize_query
from services.retrieval.vector import VectorHit
from services.retrieval.vector import VectorRetrievalInput
from services.retrieval.vector import VectorRetriever
from services.retrieval.vector import VectorSearchResult
from services.retrieval.vector import build_milvus_filter
from services.retrieval.vector import escape_milvus_string

__all__ = [
    "ChatMessage",
    "DashScopeRewriteClient",
    "DashScopeRouterClient",
    "KeywordHit",
    "KeywordRetrievalInput",
    "KeywordRetriever",
    "KeywordSearchResult",
    "LLMRewriteOutput",
    "LLMRouteResult",
    "PageRange",
    "QueryRewriter",
    "QueryRouter",
    "RewriteRequest",
    "RewriteResult",
    "RouteDecision",
    "RouteRequest",
    "RouteType",
    "SearchMode",
    "VectorHit",
    "VectorRetrievalInput",
    "VectorRetriever",
    "VectorSearchResult",
    "build_es_dsl",
    "build_milvus_filter",
    "escape_milvus_string",
    "needs_rewrite",
    "normalize_query",
]
