# -*- coding: utf-8 -*-
"""查询路由与意图分发服务。

负责在发起外部检索调用前：
1. 校验并规范化用户 query；
2. 解析限定范围内的 READY 文档与 SCHEMA_READY 结构化工作表；
3. 若范围内无任何可用数据，直接短路返回 EMPTY；
4. 调用 LLM（DashScopeRouterClient）结合候选文档与表结构 Schema 判断走 NL 还是 SQL，
   并输出检索模式（DUAL / KEYWORD_FIRST）与核心检索词；
5. 当 LLM 未配置或调用超时/异常时，自动降级到本地轻量兜底规则，不阻塞检索主链路。
"""

import logging
import re
import time
from collections.abc import Callable
from collections.abc import Sequence

from model.document import orm as document_orm
from model.document.model import Document
from model.structured_dataset import orm as dataset_orm
from model.structured_dataset.model import StructuredDataset
from services.retrieval.entry.router.llm import DashScopeRouterClient
from services.retrieval.entry.router.llm import LLMRouteResult
from services.retrieval.entry.router.models import DEFAULT_MAX_QUERY_LENGTH
from services.retrieval.entry.router.models import MAX_TOP_K
from services.retrieval.entry.router.models import RouteDecision
from services.retrieval.entry.router.models import RouteRequest
from services.retrieval.entry.router.models import RouteType
from services.retrieval.entry.router.models import SearchMode

logger = logging.getLogger("rag.retrieval.router")

_WHITESPACE = re.compile(r"\s+")
_QUOTED = re.compile(r"[\"'“”‘’《》「」『』]([^\"'“”‘’《》「」『』]{2,64})[\"'“”‘’《》「」『』]")
_CLAUSE_REF = re.compile(
    r"(?:第[一二三四五六七八九十百千万0-9]+[章节条部分款项]|\b\d+\.\d+(?:\.\d+)?\b)"
)
_CODE_TOKEN = re.compile(r"\b[A-Za-z]{1,10}[-_]\d{2,10}\b|\b[A-Z]{2,}[0-9]{2,}[A-Z0-9_-]*\b")
_STRUCTURED_FILE_TYPES = {"csv", "xlsx"}


def normalize_query(query: str, max_length: int = DEFAULT_MAX_QUERY_LENGTH) -> str:
    """去除首尾空白并折叠连续空白符；校验非空与最大长度。"""
    if not isinstance(query, str):
        raise ValueError("检索问题必须是字符串")
    cleaned = _WHITESPACE.sub(" ", query.strip())
    if not cleaned:
        raise ValueError("检索问题不能为空")
    if len(cleaned) > max_length:
        raise ValueError(f"检索问题长度不能超过 {max_length} 个字符")
    return cleaned


class QueryRouter:
    """结合可用数据范围与 LLM 意图理解，将查询分发至 NL 或 SQL 检索链路。"""

    def __init__(
        self,
        llm_client: DashScopeRouterClient | None = None,
        document_loader: Callable[
            [Sequence[str] | None, Sequence[str] | None, int], list[Document]
        ]
        | None = None,
        dataset_loader: Callable[
            [Sequence[str] | None, Sequence[str] | None], list[StructuredDataset]
        ]
        | None = None,
        max_query_length: int = DEFAULT_MAX_QUERY_LENGTH,
        ready_doc_limit: int = 10000,
    ) -> None:
        self._llm = llm_client
        self._load_documents = document_loader or document_orm.list_ready
        self._load_datasets = dataset_loader or dataset_orm.list_current_datasets
        self._max_query_length = max_query_length
        self._ready_doc_limit = ready_doc_limit

    def route(self, request: RouteRequest) -> RouteDecision:
        """执行路由判定并返回 RouteDecision。"""
        started = time.perf_counter()
        if request.top_k <= 0 or request.top_k > MAX_TOP_K:
            raise ValueError(f"top_k 必须在 1 到 {MAX_TOP_K} 之间")

        cleaned_query = normalize_query(request.query, self._max_query_length)

        # 显式传入空 document_ids 时直接短路，不查库也不调 LLM
        if request.document_ids is not None and len(request.document_ids) == 0:
            return RouteDecision(
                normalized_query=cleaned_query,
                route_type=RouteType.EMPTY,
                reason="请求的 document_ids 为空集合",
                latency_ms=_elapsed_ms(started),
                metadata={"method": "short_circuit"},
            )

        ready_docs = [
            doc
            for doc in self._load_documents(
                request.document_ids,
                request.file_types,
                self._ready_doc_limit,
            )
            if doc.file_type.lower() not in _STRUCTURED_FILE_TYPES
        ]
        datasets = self._load_datasets(request.document_ids, request.file_types)

        # 1. 范围内既无 READY 文档也无结构化工作表 -> EMPTY 短路
        if not ready_docs and not datasets:
            return RouteDecision(
                normalized_query=cleaned_query,
                route_type=RouteType.EMPTY,
                reason="当前范围内无可用 READY 文档或结构化数据集",
                latency_ms=_elapsed_ms(started),
                metadata={"method": "short_circuit"},
            )

        # 2. 优先调用 LLM 进行意图理解与路由判定
        llm_result = self._classify_with_llm(cleaned_query, ready_docs, datasets)
        if llm_result is not None:
            return _build_llm_decision(
                cleaned_query,
                llm_result,
                ready_docs,
                datasets,
                started,
            )

        # 3. LLM 未配置或调用失败时的安全降级兜底
        return _fallback_decision(cleaned_query, ready_docs, datasets, started)

    def _classify_with_llm(
        self,
        query: str,
        ready_docs: Sequence[Document],
        datasets: Sequence[StructuredDataset],
    ) -> LLMRouteResult | None:
        if self._llm is None:
            return None
        try:
            return self._llm.classify(query, ready_docs, datasets)
        except Exception as exc:
            # LLM 路由超时或故障时不阻断检索主链路，记录告警并回退
            logger.warning("LLM 查询路由失败，回退至兜底策略: %s", exc)
            return None


def _build_llm_decision(
    cleaned_query: str,
    llm_result: LLMRouteResult,
    ready_docs: Sequence[Document],
    datasets: Sequence[StructuredDataset],
    started: float,
) -> RouteDecision:
    """根据 LLM 分类结果与实际可用数据范围构造最终 RouteDecision。"""
    by_id = {ds.id: ds for ds in datasets}
    selected_datasets = [by_id[ds_id] for ds_id in llm_result.dataset_ids if ds_id in by_id]

    route_type = llm_result.route_type
    # 防止 LLM 误判到不存在的数据源类型
    if route_type == RouteType.SQL and not datasets:
        route_type = RouteType.NL
    elif route_type == RouteType.NL and not ready_docs and datasets:
        route_type = RouteType.SQL

    if route_type == RouteType.SQL and not selected_datasets:
        selected_datasets = list(datasets)

    return RouteDecision(
        normalized_query=cleaned_query,
        route_type=route_type,
        search_mode=llm_result.search_mode,
        ready_documents=tuple(ready_docs),
        matched_datasets=tuple(selected_datasets),
        extracted_keywords=llm_result.extracted_keywords,
        reason=llm_result.reason,
        latency_ms=_elapsed_ms(started),
        metadata={"method": "llm"},
    )


def _fallback_decision(
    cleaned_query: str,
    ready_docs: Sequence[Document],
    datasets: Sequence[StructuredDataset],
    started: float,
) -> RouteDecision:
    """当 LLM 不可用时，按可用数据范围与轻量特征兜底路由。"""
    search_mode, exact_tokens = _detect_exact_tokens(cleaned_query)

    if not ready_docs and datasets:
        return RouteDecision(
            normalized_query=cleaned_query,
            route_type=RouteType.SQL,
            search_mode=search_mode,
            matched_datasets=tuple(datasets),
            extracted_keywords=exact_tokens,
            reason="范围内仅存在结构化数据集（兜底路由）",
            latency_ms=_elapsed_ms(started),
            metadata={"method": "fallback"},
        )

    return RouteDecision(
        normalized_query=cleaned_query,
        route_type=RouteType.NL,
        search_mode=search_mode,
        ready_documents=tuple(ready_docs),
        matched_datasets=tuple(datasets),
        extracted_keywords=exact_tokens,
        reason="自然语言文档检索（兜底路由）",
        latency_ms=_elapsed_ms(started),
        metadata={"method": "fallback"},
    )


def _detect_exact_tokens(query: str) -> tuple[SearchMode, tuple[str, ...]]:
    """兜底提取引号短语、法条章节号或编码标识符。"""
    tokens: list[str] = []
    for pattern in (_QUOTED, _CLAUSE_REF, _CODE_TOKEN):
        for match in pattern.finditer(query):
            val = (match.group(1) if pattern is _QUOTED else match.group(0)).strip()
            if val and val not in tokens:
                tokens.append(val)
    if tokens:
        return SearchMode.KEYWORD_FIRST, tuple(tokens)
    return SearchMode.DUAL, ()


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
