# -*- coding: utf-8 -*-
"""DashScope 文本精排客户端。

官方接口把 query 和 documents 放在 input 内，top_n 放在 parameters 内。
返回的 index 对应当次 documents 下标，relevance_score 只在本次请求内比较。
"""

import logging
from collections.abc import Sequence
from typing import Protocol

import httpx

from config import Settings
from services.retrieval.rag.rerank.models import DEFAULT_RERANK_CONNECT_TIMEOUT_SECONDS
from services.retrieval.rag.rerank.models import DEFAULT_RERANK_MODEL
from services.retrieval.rag.rerank.models import DEFAULT_RERANK_TIMEOUT_SECONDS
from services.retrieval.rag.rerank.models import RERANK_AUTH_FAILED
from services.retrieval.rag.rerank.models import RERANK_BAD_RESPONSE
from services.retrieval.rag.rerank.models import RERANK_ERROR
from services.retrieval.rag.rerank.models import RERANK_INPUT_TOO_LONG
from services.retrieval.rag.rerank.models import RERANK_RATE_LIMITED
from services.retrieval.rag.rerank.models import RERANK_TIMEOUT
from services.retrieval.rag.rerank.models import RerankCallError

logger = logging.getLogger("rag.retrieval.rerank")

_RERANK_PATH = "/services/rerank/text-rerank/text-rerank"
_AUTH_CODES = frozenset({"InvalidApiKey", "AccessDenied", "Unauthorized"})
_TOO_LONG_MARKERS = (
    "token",
    "length",
    "too long",
    "exceed",
    "range of input",
    "超长",
    "长度",
)


class RerankProvider(Protocol):
    """精排供应商。返回 (候选下标, relevance_score)，按分数降序。"""

    model: str

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        """对 documents 打分。下标对应本次传入的列表，而不是父块原排名。"""


class DashScopeRerankClient:
    """调用阿里云百炼文本排序接口。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str | None = None,
        base_url: str = "https://dashscope.aliyuncs.com/api/v1",
        model: str = DEFAULT_RERANK_MODEL,
        timeout_seconds: float = DEFAULT_RERANK_TIMEOUT_SECONDS,
        connect_timeout_seconds: float = DEFAULT_RERANK_CONNECT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if settings is not None:
            api_key = settings.dashscope_api_key
            base_url = settings.rerank_base_url
            model = settings.rerank_model
            timeout_seconds = settings.rerank_timeout_seconds
            connect_timeout_seconds = settings.rerank_connect_timeout_seconds
        self.api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.transport = transport

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        """发送一次精排请求，并只保留下标和相关性分数。"""
        if not self.api_key:
            raise RerankCallError(RERANK_AUTH_FAILED, "未配置 DASHSCOPE_API_KEY")
        if not documents:
            raise RerankCallError(RERANK_ERROR, "精排文档列表为空")
        if top_n < 1:
            raise RerankCallError(RERANK_ERROR, "top_n 必须大于 0")

        try:
            with httpx.Client(
                transport=self.transport,
                timeout=httpx.Timeout(
                    self.timeout_seconds,
                    connect=self.connect_timeout_seconds,
                ),
            ) as client:
                response = client.post(
                    f"{self.base_url}{_RERANK_PATH}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "input": {
                            "query": query,
                            "documents": list(documents),
                        },
                        "parameters": {
                            "top_n": top_n,
                            "return_documents": False,
                        },
                    },
                )
        except httpx.TimeoutException as exc:
            raise RerankCallError(
                RERANK_TIMEOUT,
                "rerank 请求超时",
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise RerankCallError(
                RERANK_TIMEOUT,
                "rerank 网络失败",
                retryable=True,
            ) from exc

        payload = _response_payload(response)
        if isinstance(payload, dict) and payload.get("code"):
            raise _error_from_payload(response.status_code, payload)
        if not response.is_success:
            raise _error_from_payload(
                response.status_code,
                payload if isinstance(payload, dict) else {},
            )
        if not isinstance(payload, dict):
            raise RerankCallError(RERANK_BAD_RESPONSE, "rerank 响应不是 JSON 对象")
        return _parse_results(payload)


def _response_payload(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return {}


def _error_from_payload(status_code: int, payload: dict[str, object]) -> RerankCallError:
    code = str(payload.get("code") or "")
    message = _short_message(payload.get("message") or f"HTTP {status_code}")
    if status_code in {401, 403} or code in _AUTH_CODES:
        return RerankCallError(RERANK_AUTH_FAILED, message)
    if status_code == 429 or code.startswith("Throttling"):
        return RerankCallError(RERANK_RATE_LIMITED, message, retryable=True)
    if _is_input_too_long(status_code, code, message):
        return RerankCallError(RERANK_INPUT_TOO_LONG, message, retryable=True)
    logger.warning("rerank 请求失败 status=%s code=%s", status_code, code or "-")
    return RerankCallError(RERANK_ERROR, message)


def _is_input_too_long(status_code: int, code: str, message: str) -> bool:
    if status_code not in {400, 413}:
        return False
    haystack = f"{code} {message}".lower()
    return any(marker in haystack for marker in _TOO_LONG_MARKERS)


def _parse_results(payload: dict[str, object]) -> list[tuple[int, float]]:
    output = payload.get("output")
    if not isinstance(output, dict):
        raise RerankCallError(RERANK_BAD_RESPONSE, "rerank 响应缺少 output")
    results = output.get("results")
    if not isinstance(results, list) or not results:
        raise RerankCallError(RERANK_BAD_RESPONSE, "rerank 响应缺少 results")

    parsed: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            raise RerankCallError(RERANK_BAD_RESPONSE, "rerank 结果项不是对象")
        index = item.get("index")
        score = item.get("relevance_score")
        if isinstance(index, bool) or not isinstance(index, int):
            raise RerankCallError(RERANK_BAD_RESPONSE, "rerank 结果下标无效")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise RerankCallError(RERANK_BAD_RESPONSE, "rerank 结果分数无效")
        parsed.append((index, float(score)))
    parsed.sort(key=lambda item: item[1], reverse=True)
    return parsed


def _short_message(message: object) -> str:
    text = str(message).replace("\n", " ").strip()
    if len(text) > 120:
        return text[:120]
    return text
