# -*- coding: utf-8 -*-
"""父块精排服务。

只对 RRF 前列父块调用一次 rerank。分数写入 rerank_score，成功时 score 改用该分数；
失败、超时或熔断时保持原来的 RRF 顺序，不中断检索。
"""

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import replace

from config import Settings
from services.retrieval.fusion.backfill.models import FusedParent
from services.retrieval.rerank.client import DashScopeRerankClient
from services.retrieval.rerank.client import RerankProvider
from services.retrieval.rerank.models import DEFAULT_RERANK_CANDIDATES
from services.retrieval.rerank.models import DEFAULT_RERANK_CIRCUIT_OPEN_SECONDS
from services.retrieval.rerank.models import DEFAULT_RERANK_CIRCUIT_THRESHOLD
from services.retrieval.rerank.models import DEFAULT_RERANK_CIRCUIT_WINDOW_SECONDS
from services.retrieval.rerank.models import DEFAULT_RERANK_MAX_DOC_CHARS
from services.retrieval.rerank.models import DEFAULT_RERANK_MAX_RETRIES
from services.retrieval.rerank.models import DEFAULT_RERANK_MODEL
from services.retrieval.rerank.models import DEFAULT_RERANK_TOP_N
from services.retrieval.rerank.models import RERANK_BAD_RESPONSE
from services.retrieval.rerank.models import RERANK_CIRCUIT_OPEN
from services.retrieval.rerank.models import RERANK_ERROR
from services.retrieval.rerank.models import RERANK_INPUT_TOO_LONG
from services.retrieval.rerank.models import RERANK_SKIPPED_EMPTY
from services.retrieval.rerank.models import RerankCallError
from services.retrieval.rerank.models import RerankInput
from services.retrieval.rerank.models import RerankOutput

logger = logging.getLogger("rag.retrieval.rerank")

_BOUNDARY_MARKS = ("\n", "。", ".")


def truncate_document(text: str, max_chars: int) -> str:
    """保留开头，并把截断点对齐到窗口内最近的句号或换行。"""
    if max_chars < 1:
        raise ValueError("max_chars 必须大于 0")
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    boundary = max(window.rfind(mark) for mark in _BOUNDARY_MARKS)
    if boundary < 0:
        return window
    return window[: boundary + 1]


class Reranker:
    """按配置对父块候选做一次精排，失败时回退 RRF。"""

    def __init__(
        self,
        provider: RerankProvider | None = None,
        *,
        enabled: bool = False,
        candidate_limit: int = DEFAULT_RERANK_CANDIDATES,
        top_n: int = DEFAULT_RERANK_TOP_N,
        max_doc_chars: int = DEFAULT_RERANK_MAX_DOC_CHARS,
        max_retries: int = DEFAULT_RERANK_MAX_RETRIES,
        retry_backoff_seconds: float = 0.2,
        circuit_threshold: int = DEFAULT_RERANK_CIRCUIT_THRESHOLD,
        circuit_window_seconds: float = DEFAULT_RERANK_CIRCUIT_WINDOW_SECONDS,
        circuit_open_seconds: float = DEFAULT_RERANK_CIRCUIT_OPEN_SECONDS,
        model: str = DEFAULT_RERANK_MODEL,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._enabled = enabled
        self._candidate_limit = candidate_limit
        self._top_n = top_n
        self._max_doc_chars = max_doc_chars
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._circuit = _FailureCircuit(
            circuit_threshold,
            circuit_window_seconds,
            circuit_open_seconds,
            clock,
        )
        provider_model = getattr(provider, "model", "") if provider is not None else ""
        self._model = str(provider_model or model)
        self._sleep = sleeper

    @classmethod
    def from_settings(cls, settings: Settings) -> "Reranker":
        """用进程配置构造默认的 DashScope 精排器。"""
        return cls(
            DashScopeRerankClient(settings),
            enabled=settings.retrieval_rerank_enabled,
            candidate_limit=settings.retrieval_rerank_candidates,
            top_n=settings.retrieval_rerank_top_n,
            max_doc_chars=settings.rerank_max_doc_chars,
            max_retries=settings.rerank_max_retries,
            circuit_threshold=settings.rerank_failure_circuit_threshold,
            circuit_open_seconds=settings.rerank_circuit_open_seconds,
            model=settings.rerank_model,
        )

    def rerank(self, request: RerankInput) -> RerankOutput:
        """精排父块。服务端开关和请求参数都打开时才调用供应商。"""
        started = time.perf_counter()
        if not self._enabled or not request.requested:
            return self._finish(request.candidates, False, None, started)

        if not request.candidates:
            return self._finish((), False, RERANK_SKIPPED_EMPTY, started)

        if not self._circuit.allow():
            logger.warning("rerank 熔断中，回退 RRF request_id=%s", request.request_id)
            return self._finish(request.candidates, False, RERANK_CIRCUIT_OPEN, started)

        query = request.query.strip()
        if not query or self._provider is None:
            logger.warning("rerank 缺少 query 或供应商 request_id=%s", request.request_id)
            return self._finish(request.candidates, False, RERANK_ERROR, started)

        window = tuple(request.candidates[: self._candidate_limit])
        requested_top_n = request.top_n if request.top_n > 0 else self._top_n
        effective_top_n = min(requested_top_n, self._top_n, len(window))
        if not window or effective_top_n < 1:
            return self._finish(request.candidates, False, RERANK_ERROR, started)
        try:
            scores = self._call_with_retry(
                query,
                window,
                effective_top_n,
                request.request_id,
            )
            ranked = _apply_scores(window, scores, effective_top_n)
        except RerankCallError as exc:
            self._circuit.record_failure()
            logger.warning(
                "rerank 回退 RRF request_id=%s query_hash=%s code=%s",
                request.request_id,
                _query_hash(query),
                exc.code,
            )
            return self._finish(request.candidates, False, exc.code, started)
        except Exception as exc:
            # 供应商的意外异常只降级本段精排，检索仍返回 RRF 结果。
            self._circuit.record_failure()
            logger.warning(
                "rerank 未预期失败 request_id=%s error=%s",
                request.request_id,
                type(exc).__name__,
            )
            return self._finish(request.candidates, False, RERANK_ERROR, started)

        self._circuit.record_success()
        logger.info(
            "rerank 完成 request_id=%s query_hash=%s count=%s latency_ms=%s",
            request.request_id,
            _query_hash(query),
            len(ranked),
            _elapsed_ms(started),
        )
        return self._finish(ranked, True, None, started)

    def _call_with_retry(
        self,
        query: str,
        window: Sequence[FusedParent],
        top_n: int,
        request_id: str,
    ) -> list[tuple[int, float]]:
        provider = self._provider
        if provider is None:
            raise RerankCallError(RERANK_ERROR, "未配置 rerank 供应商")
        max_chars = self._max_doc_chars
        attempt = 0
        while True:
            documents = _documents_for_rerank(window, max_chars, request_id)
            try:
                return provider.rerank(query, documents, top_n)
            except RerankCallError as exc:
                if not exc.retryable or attempt >= self._max_retries:
                    raise
                attempt += 1
                if exc.code == RERANK_INPUT_TOO_LONG:
                    max_chars = max(1, max_chars // 2)
                logger.warning(
                    "rerank 重试 request_id=%s code=%s attempt=%s",
                    request_id,
                    exc.code,
                    attempt,
                )
                self._sleep(self._retry_backoff_seconds * attempt)

    def _finish(
        self,
        ranked: Sequence[FusedParent],
        reranked: bool,
        rerank_error: str | None,
        started: float,
    ) -> RerankOutput:
        return RerankOutput(
            ranked=tuple(ranked),
            reranked=reranked,
            rerank_error=rerank_error,
            model=self._model,
            call_latency_ms=_elapsed_ms(started),
        )


def _documents_for_rerank(
    window: Sequence[FusedParent],
    max_chars: int,
    request_id: str,
) -> list[str]:
    documents: list[str] = []
    for parent in window:
        text = truncate_document(parent.content, max_chars)
        if len(text) != len(parent.content):
            logger.info(
                "rerank 文档截断 request_id=%s parent_id=%s original_chars=%s truncated_chars=%s",
                request_id,
                parent.parent_id,
                len(parent.content),
                len(text),
            )
        documents.append(text)
    return documents


def _apply_scores(
    window: Sequence[FusedParent],
    scores: Sequence[tuple[int, float]],
    top_n: int,
) -> tuple[FusedParent, ...]:
    by_index: dict[int, float] = {}
    for index, score in scores:
        if index < 0 or index >= len(window):
            continue
        current = by_index.get(index)
        if current is None or score > current:
            by_index[index] = score
    if not by_index:
        raise RerankCallError(RERANK_BAD_RESPONSE, "rerank 结果没有有效下标")

    ordered = sorted(by_index, key=lambda index: (-by_index[index], index))
    ranked: list[FusedParent] = []
    for rank, index in enumerate(ordered[:top_n], start=1):
        score = by_index[index]
        ranked.append(
            replace(
                window[index],
                score=score,
                rerank_score=score,
                rank=rank,
            )
        )
    return tuple(ranked)


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


class _FailureCircuit:
    """同一进程内，一分钟失败次数达到阈值后暂停调用精排。"""

    def __init__(
        self,
        threshold: int,
        window_seconds: float,
        open_seconds: float,
        clock: Callable[[], float],
    ) -> None:
        self._threshold = threshold
        self._window_seconds = window_seconds
        self._open_seconds = open_seconds
        self._clock = clock
        self._failures: list[float] = []
        self._opened_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            return self._clock() >= self._opened_until

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()

    def record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            self._failures = [
                item for item in self._failures if now - item < self._window_seconds
            ]
            self._failures.append(now)
            if len(self._failures) < self._threshold:
                return
            self._opened_until = now + self._open_seconds
            self._failures.clear()
            logger.warning(
                "rerank 熔断开启 open_seconds=%s",
                self._open_seconds,
            )
