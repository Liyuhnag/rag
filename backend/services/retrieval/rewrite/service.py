# -*- coding: utf-8 -*-
"""查询理解与改写服务（QueryRewriter）。

负责在进入查询路由器（QueryRouter）前：
1. 判断是否需要改写（首轮自包含且无指代/相对时间/多意图并列的问题直接走快速路径 skipped）；
2. 对多轮追问、含代词指代、口语化或复合多意图问题，调用 LLM 执行指代消解、规范化改写与子问题拆解；
3. 当 LLM 返回低置信度（< 0.6）或请求超时/失败时，安全回退到原始问题（fallback）。
"""

import logging
import re
import time

from services.retrieval.rewrite.llm import DashScopeRewriteClient
from services.retrieval.rewrite.models import DEFAULT_MIN_CONFIDENCE
from services.retrieval.rewrite.models import RewriteRequest
from services.retrieval.rewrite.models import RewriteResult
from services.retrieval.router.models import DEFAULT_MAX_QUERY_LENGTH
from services.retrieval.router.service import normalize_query

logger = logging.getLogger("rag.retrieval.rewrite")

# 触发改写的特征：指代词、相对时间词、多意图并列或短追问
_PRONOUN_OR_REF = re.compile(
    r"(?:它|他|她|其|该|此|这个|那个|这些|那些|上面|前面|刚才|上述|前述|那里|这儿|呢\s*[？?]?$)"
)
_RELATIVE_TIME = re.compile(
    r"(?:今年|去年|明年|前年|上个月|这个月|下个月|上季度|本季度|下季度|最近|近期|刚才)"
)
_MULTI_INTENT = re.compile(
    r"(?:分别|各自|对比|比较|以及|并且|还有|另外|同时|差多少.*为什么|是多少.*为什么)"
)


def needs_rewrite(question: str, has_history: bool) -> bool:
    """判断是否需要调用 LLM 改写；首轮自包含且无特殊触发词时走快速路径跳过。"""
    if has_history:
        return True
    if len(question) < 10:
        return True
    if _PRONOUN_OR_REF.search(question):
        return True
    if _RELATIVE_TIME.search(question):
        return True
    if _MULTI_INTENT.search(question):
        return True
    return False


class QueryRewriter:
    """在路由前完成指代消解、口语规范化与子问题拆解。"""

    def __init__(
        self,
        llm_client: DashScopeRewriteClient | None = None,
        max_query_length: int = DEFAULT_MAX_QUERY_LENGTH,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> None:
        self._llm = llm_client
        self._max_query_length = max_query_length
        self._min_confidence = min_confidence

    def rewrite(self, request: RewriteRequest) -> RewriteResult:
        """执行查询改写并返回 RewriteResult。"""
        started = time.perf_counter()
        cleaned = normalize_query(request.question, self._max_query_length)
        has_history = bool(request.conversation_history or request.conversation_summary)

        # 1. 快速路径：首轮自包含、无指代/相对时间/并列拆解需求时直接跳过 LLM
        if not needs_rewrite(cleaned, has_history):
            return RewriteResult(
                original_question=cleaned,
                rewritten_query=cleaned,
                file_types=request.default_file_types,
                method="skipped",
                latency_ms=_elapsed_ms(started),
            )

        # 2. 未配置 LLM 客户端时回退原始问题
        if self._llm is None:
            return RewriteResult(
                original_question=cleaned,
                rewritten_query=cleaned,
                file_types=request.default_file_types,
                method="fallback",
                latency_ms=_elapsed_ms(started),
            )

        # 3. 调用 LLM 执行改写
        try:
            output = self._llm.rewrite(
                cleaned,
                request.conversation_history,
                request.conversation_summary,
            )
        except Exception as exc:
            logger.warning("LLM 查询改写失败，回退至原始问题: %s", exc)
            return RewriteResult(
                original_question=cleaned,
                rewritten_query=cleaned,
                file_types=request.default_file_types,
                method="fallback",
                latency_ms=_elapsed_ms(started),
            )

        # 4. 置信度过低或改写结果超过长度上限时，回退原始问题
        if (
            output.confidence < self._min_confidence
            or not output.rewritten_query
            or len(output.rewritten_query) > self._max_query_length
        ):
            return RewriteResult(
                original_question=cleaned,
                rewritten_query=cleaned,
                file_types=request.default_file_types,
                is_follow_up=output.is_follow_up,
                confidence=output.confidence,
                method="fallback",
                latency_ms=_elapsed_ms(started),
            )

        # 请求显式传入的 default_file_types 优先于 LLM 抽取结果
        merged_file_types = (
            request.default_file_types
            if request.default_file_types
            else (output.file_types or None)
        )

        return RewriteResult(
            original_question=cleaned,
            rewritten_query=output.rewritten_query,
            sub_queries=output.sub_queries,
            file_types=merged_file_types,
            keywords=output.keywords,
            is_follow_up=output.is_follow_up,
            confidence=output.confidence,
            method="llm",
            latency_ms=_elapsed_ms(started),
        )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
