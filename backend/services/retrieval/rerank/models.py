# -*- coding: utf-8 -*-
"""父块精排的输入、输出和供应商错误。"""

from dataclasses import dataclass

from services.retrieval.fusion.backfill.models import FusedParent

RERANK_AUTH_FAILED = "RERANK_AUTH_FAILED"
RERANK_RATE_LIMITED = "RERANK_RATE_LIMITED"
RERANK_TIMEOUT = "RERANK_TIMEOUT"
RERANK_INPUT_TOO_LONG = "RERANK_INPUT_TOO_LONG"
RERANK_BAD_RESPONSE = "RERANK_BAD_RESPONSE"
RERANK_SKIPPED_EMPTY = "RERANK_SKIPPED_EMPTY"
RERANK_CIRCUIT_OPEN = "RERANK_CIRCUIT_OPEN"
RERANK_ERROR = "RERANK_ERROR"

DEFAULT_RERANK_MODEL = "gte-rerank-v2"
DEFAULT_RERANK_CANDIDATES = 20
DEFAULT_RERANK_TOP_N = 10
DEFAULT_RERANK_TIMEOUT_SECONDS = 15
DEFAULT_RERANK_CONNECT_TIMEOUT_SECONDS = 5
DEFAULT_RERANK_MAX_RETRIES = 1
DEFAULT_RERANK_MAX_DOC_CHARS = 4000
DEFAULT_RERANK_CIRCUIT_THRESHOLD = 5
DEFAULT_RERANK_CIRCUIT_WINDOW_SECONDS = 60
DEFAULT_RERANK_CIRCUIT_OPEN_SECONDS = 300


class RerankCallError(Exception):
    """精排供应商调用失败。服务层据此决定重试或回退 RRF。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class RerankInput:
    """进入精排的 query 与已经回查正文的父块。"""

    query: str
    candidates: tuple[FusedParent, ...] = ()
    top_n: int = DEFAULT_RERANK_TOP_N
    request_id: str = ""
    requested: bool = False


@dataclass(frozen=True)
class RerankOutput:
    """精排结果。失败时 ranked 保持 RRF 原顺序。"""

    ranked: tuple[FusedParent, ...] = ()
    reranked: bool = False
    rerank_error: str | None = None
    model: str = ""
    call_latency_ms: int = 0
