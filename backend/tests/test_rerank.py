# -*- coding: utf-8 -*-
"""父块精排：截断、分数、降级、熔断和 DashScope 请求映射。"""

import json

import httpx
import pytest

import config
from config import Settings
from services.retrieval import DashScopeRerankClient
from services.retrieval import FusedParent
from services.retrieval import RerankCallError
from services.retrieval import RerankInput
from services.retrieval import Reranker
from services.retrieval.rag.rerank.service import truncate_document


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeProvider:
    def __init__(self, script: list[object]) -> None:
        self.model = "gte-rerank-v2"
        self.script = list(script)
        self.calls: list[tuple[str, list[str], int]] = []

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        self.calls.append((query, list(documents), top_n))
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def _parent(parent_id: str, content: str, rrf_score: float, rank: int) -> FusedParent:
    return FusedParent(
        parent_id=parent_id,
        document_id="doc-1",
        document_name="制度.pdf",
        version=1,
        parent_index=rank,
        file_type="pdf",
        content=content,
        heading_path="第一章",
        locator="p1",
        page_start=1,
        page_end=1,
        token_count=10,
        score=rrf_score,
        rrf_score=rrf_score,
        rerank_score=None,
        rank=rank,
        matched_child_count=1,
        matched_children=(),
    )


def _request(*parents: FusedParent, top_n: int = 10, query: str = "住宿标准") -> RerankInput:
    return RerankInput(
        query=query,
        candidates=parents,
        top_n=top_n,
        request_id="req-1",
        requested=True,
    )


def test_truncate_document_keeps_start_and_aligns_to_period_or_newline():
    assert truncate_document("短文本", 10) == "短文本"
    assert truncate_document("第一句。第二句继续写下去", 8) == "第一句。"
    assert truncate_document("标题\n正文没有句号但是很长", 6) == "标题\n"
    assert truncate_document("Hello world. More text", 14) == "Hello world."
    plain = "没有句号也没有换行的正文"
    assert truncate_document(plain, 4) == "没有句号"
    assert len(truncate_document(plain, 4)) <= 4


def test_disabled_rerank_does_not_call_provider():
    provider = FakeProvider([[(0, 0.9)]])
    reranker = Reranker(provider, enabled=False)
    parents = (_parent("p1", "甲", 0.4, 1),)
    result = reranker.rerank(_request(*parents))
    assert not result.reranked
    assert result.rerank_error is None
    assert result.ranked == parents
    assert provider.calls == []


def test_request_flag_off_does_not_call_provider():
    provider = FakeProvider([[(0, 0.9)]])
    reranker = Reranker(provider, enabled=True)
    parents = (_parent("p1", "甲", 0.4, 1),)
    result = reranker.rerank(
        RerankInput(query="住宿标准", candidates=parents, requested=False)
    )
    assert not result.reranked
    assert result.rerank_error is None
    assert provider.calls == []


def test_empty_candidates_skip_without_calling_provider():
    provider = FakeProvider([[(0, 0.9)]])
    reranker = Reranker(provider, enabled=True)
    result = reranker.rerank(_request())
    assert not result.reranked
    assert result.rerank_error == "RERANK_SKIPPED_EMPTY"
    assert result.ranked == ()
    assert provider.calls == []


def test_rerank_reorders_by_score_and_keeps_rrf_score():
    provider = FakeProvider([[(2, 0.4), (0, 0.4), (1, 0.9)]])
    reranker = Reranker(provider, enabled=True)
    parents = (
        _parent("a", "甲", 0.05, 1),
        _parent("b", "乙", 0.04, 2),
        _parent("c", "丙", 0.03, 3),
    )
    result = reranker.rerank(_request(*parents, top_n=3))
    assert result.reranked
    assert result.rerank_error is None
    assert result.model == "gte-rerank-v2"
    assert [item.parent_id for item in result.ranked] == ["b", "a", "c"]
    assert [item.rank for item in result.ranked] == [1, 2, 3]
    assert result.ranked[0].score == result.ranked[0].rerank_score == 0.9
    assert result.ranked[0].rrf_score == 0.04
    assert result.ranked[1].rerank_score == result.ranked[2].rerank_score == 0.4
    assert result.ranked[0].content == "乙"


def test_top_n_and_candidate_window_limit_the_provider_call():
    provider = FakeProvider([[(1, 0.8), (0, 0.2)]])
    reranker = Reranker(provider, enabled=True, candidate_limit=2, top_n=1)
    parents = (
        _parent("a", "甲", 0.05, 1),
        _parent("b", "乙", 0.04, 2),
        _parent("c", "丙", 0.03, 3),
    )
    result = reranker.rerank(_request(*parents, top_n=10))
    assert provider.calls[0][1] == ["甲", "乙"]
    assert provider.calls[0][2] == 1
    assert [item.parent_id for item in result.ranked] == ["b"]
    assert result.ranked[0].rank == 1


def test_truncation_is_sent_to_provider_without_changing_parent_content():
    provider = FakeProvider([[(0, 0.6)]])
    reranker = Reranker(provider, enabled=True, max_doc_chars=4)
    content = "没有句号也没有换行"
    result = reranker.rerank(_request(_parent("p1", content, 0.2, 1)))
    assert provider.calls[0][1] == ["没有句号"]
    assert result.ranked[0].content == content
    assert result.ranked[0].rerank_score == 0.6


def test_timeout_retries_once_then_falls_back_to_rrf_order():
    timeout = RerankCallError("RERANK_TIMEOUT", "超时", retryable=True)
    provider = FakeProvider([timeout, timeout])
    sleeps: list[float] = []
    reranker = Reranker(
        provider,
        enabled=True,
        max_retries=1,
        circuit_threshold=99,
        sleeper=sleeps.append,
    )
    parents = (_parent("p1", "甲", 0.4, 1), _parent("p2", "乙", 0.2, 2))
    result = reranker.rerank(_request(*parents))
    assert not result.reranked
    assert result.rerank_error == "RERANK_TIMEOUT"
    assert result.ranked == parents
    assert result.ranked[0].rerank_score is None
    assert result.ranked[0].score == 0.4
    assert len(provider.calls) == 2
    assert sleeps == [0.2]


def test_auth_failure_does_not_retry():
    provider = FakeProvider(
        [RerankCallError("RERANK_AUTH_FAILED", "鉴权失败", retryable=False)]
    )
    sleeps: list[float] = []
    reranker = Reranker(provider, enabled=True, sleeper=sleeps.append)
    parents = (_parent("p1", "甲", 0.4, 1),)
    result = reranker.rerank(_request(*parents))
    assert result.rerank_error == "RERANK_AUTH_FAILED"
    assert result.ranked == parents
    assert provider.calls == [("住宿标准", ["甲"], 1)]
    assert sleeps == []


def test_input_too_long_retries_with_shorter_document():
    class LengthProvider:
        model = "gte-rerank-v2"

        def __init__(self) -> None:
            self.lengths: list[int] = []

        def rerank(
            self,
            query: str,
            documents: list[str],
            top_n: int,
        ) -> list[tuple[int, float]]:
            self.lengths.append(len(documents[0]))
            if len(documents[0]) > 20:
                raise RerankCallError("RERANK_INPUT_TOO_LONG", "超长", retryable=True)
            return [(0, 0.66)]

    provider = LengthProvider()
    reranker = Reranker(
        provider,
        enabled=True,
        max_doc_chars=40,
        max_retries=1,
        sleeper=lambda _seconds: None,
    )
    content = "甲" * 40
    result = reranker.rerank(_request(_parent("p1", content, 0.2, 1)))
    assert provider.lengths == [40, 20]
    assert result.reranked
    assert result.ranked[0].content == content
    assert result.ranked[0].rerank_score == 0.66


def test_invalid_index_falls_back_without_retry():
    provider = FakeProvider([[(9, 0.9)]])
    reranker = Reranker(provider, enabled=True, max_retries=1)
    parents = (_parent("p1", "甲", 0.4, 1),)
    result = reranker.rerank(_request(*parents))
    assert result.rerank_error == "RERANK_BAD_RESPONSE"
    assert result.ranked == parents
    assert len(provider.calls) == 1


def test_unexpected_provider_error_falls_back_to_rrf():
    class Boom:
        model = "gte-rerank-v2"

        def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
            raise RuntimeError("provider failed")

    reranker = Reranker(Boom(), enabled=True, max_retries=1)
    parents = (_parent("p1", "甲", 0.4, 1),)
    result = reranker.rerank(_request(*parents))
    assert result.rerank_error == "RERANK_ERROR"
    assert result.ranked == parents


def test_failures_inside_one_minute_open_circuit():
    clock = ManualClock()
    provider = FakeProvider(
        [RerankCallError("RERANK_TIMEOUT", "超时", retryable=True)] * 5
    )
    reranker = Reranker(
        provider,
        enabled=True,
        max_retries=0,
        circuit_threshold=5,
        circuit_open_seconds=300,
        clock=clock,
        sleeper=lambda _seconds: None,
    )
    parents = (_parent("p1", "甲", 0.3, 1),)
    request = _request(*parents)
    for _ in range(5):
        result = reranker.rerank(request)
        assert result.rerank_error == "RERANK_TIMEOUT"
    blocked = reranker.rerank(request)
    assert blocked.rerank_error == "RERANK_CIRCUIT_OPEN"
    assert blocked.ranked == parents
    assert len(provider.calls) == 5

    clock.now = 299
    still_blocked = reranker.rerank(request)
    assert still_blocked.rerank_error == "RERANK_CIRCUIT_OPEN"
    assert len(provider.calls) == 5

    clock.now = 300
    provider.script.append([(0, 0.7)])
    recovered = reranker.rerank(request)
    assert recovered.reranked
    assert recovered.ranked[0].rerank_score == 0.7
    assert len(provider.calls) == 6


def test_failures_older_than_one_minute_do_not_open_circuit():
    clock = ManualClock()
    provider = FakeProvider(
        [RerankCallError("RERANK_TIMEOUT", "超时", retryable=False)] * 8
    )
    reranker = Reranker(
        provider,
        enabled=True,
        max_retries=0,
        circuit_threshold=5,
        clock=clock,
        sleeper=lambda _seconds: None,
    )
    request = _request(_parent("p1", "甲", 0.3, 1))
    for _ in range(4):
        assert reranker.rerank(request).rerank_error == "RERANK_TIMEOUT"
    clock.now = 61
    for _ in range(4):
        assert reranker.rerank(request).rerank_error == "RERANK_TIMEOUT"
    assert len(provider.calls) == 8


def test_dashscope_client_posts_rerank_payload_and_sorts_scores():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 1, "relevance_score": 0.2},
                        {"index": 0, "relevance_score": 0.9},
                    ]
                },
                "request_id": "remote-1",
            },
        )

    client = DashScopeRerankClient(
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )
    assert client.rerank("问题", ["甲", "乙"], 2) == [(0, 0.9), (1, 0.2)]
    assert str(captured["url"]).endswith("/services/rerank/text-rerank/text-rerank")
    assert captured["auth"] == "Bearer sk-test"
    body = captured["body"]
    assert body["model"] == "gte-rerank-v2"
    assert body["input"] == {"query": "问题", "documents": ["甲", "乙"]}
    assert body["parameters"] == {"top_n": 2, "return_documents": False}


def test_dashscope_client_maps_auth_rate_limit_timeout_and_bad_body():
    def response_for(status: int, payload: dict[str, object]) -> DashScopeRerankClient:
        return DashScopeRerankClient(
            api_key="sk-test",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json=payload)
            ),
        )

    with pytest.raises(RerankCallError) as auth_error:
        response_for(
            401,
            {"code": "InvalidApiKey", "message": "Invalid API-key provided."},
        ).rerank("问题", ["甲"], 1)
    assert auth_error.value.code == "RERANK_AUTH_FAILED"
    assert not auth_error.value.retryable

    with pytest.raises(RerankCallError) as rate_error:
        response_for(
            429,
            {"code": "Throttling.RateQuota", "message": "Requests rate limit exceeded."},
        ).rerank("问题", ["甲"], 1)
    assert rate_error.value.code == "RERANK_RATE_LIMITED"
    assert rate_error.value.retryable

    with pytest.raises(RerankCallError) as length_error:
        response_for(
            400,
            {
                "code": "InvalidParameter",
                "message": "Range of input length should be [1, 4000]",
            },
        ).rerank("问题", ["甲"], 1)
    assert length_error.value.code == "RERANK_INPUT_TOO_LONG"
    assert length_error.value.retryable

    with pytest.raises(RerankCallError) as bad_body:
        response_for(200, {"output": {"results": [{"index": "bad"}]}}).rerank(
            "问题",
            ["甲"],
            1,
        )
    assert bad_body.value.code == "RERANK_BAD_RESPONSE"
    assert not bad_body.value.retryable

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    client = DashScopeRerankClient(
        api_key="sk-test",
        transport=httpx.MockTransport(timeout),
    )
    with pytest.raises(RerankCallError) as timeout_error:
        client.rerank("问题", ["甲"], 1)
    assert timeout_error.value.code == "RERANK_TIMEOUT"
    assert timeout_error.value.retryable


def test_missing_api_key_does_not_call_dashscope():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("未配置密钥时不应发请求")

    client = DashScopeRerankClient(
        api_key="",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RerankCallError) as caught:
        client.rerank("问题", ["甲"], 1)
    assert caught.value.code == "RERANK_AUTH_FAILED"
    assert not caught.value.retryable


def test_from_settings_stays_off_until_enabled():
    disabled = Reranker.from_settings(Settings())
    parents = (_parent("p1", "甲", 0.4, 1),)
    skipped = disabled.rerank(_request(*parents))
    assert not skipped.reranked
    assert skipped.rerank_error is None
    assert skipped.model == "gte-rerank-v2"

    enabled = Reranker.from_settings(Settings(retrieval_rerank_enabled=True))
    failed = enabled.rerank(_request(*parents))
    assert failed.rerank_error == "RERANK_AUTH_FAILED"
    assert failed.ranked == parents


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)
    for key in (
        "S3_ENDPOINT",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_BUCKET",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_DATABASE",
    ):
        monkeypatch.setenv(key, "test")
    for key in (
        "RETRIEVAL_RERANK_ENABLED",
        "RERANK_BASE_URL",
        "RERANK_MODEL",
        "RETRIEVAL_RERANK_CANDIDATES",
        "RETRIEVAL_RERANK_TOP_N",
        "RERANK_TIMEOUT_SECONDS",
        "RERANK_CONNECT_TIMEOUT_SECONDS",
        "RERANK_MAX_RETRIES",
        "RERANK_MAX_DOC_CHARS",
        "RERANK_FAILURE_CIRCUIT_THRESHOLD",
        "RERANK_CIRCUIT_OPEN_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_rerank_settings_default_off(env):
    settings = config.load_settings()
    assert not settings.retrieval_rerank_enabled
    assert settings.rerank_model == "gte-rerank-v2"
    assert settings.rerank_base_url == "https://dashscope.aliyuncs.com/api/v1"
    assert settings.retrieval_rerank_candidates == 20
    assert settings.retrieval_rerank_top_n == 10
    assert settings.rerank_timeout_seconds == 15
    assert settings.rerank_connect_timeout_seconds == 5
    assert settings.rerank_max_retries == 1
    assert settings.rerank_max_doc_chars == 4000
    assert settings.rerank_failure_circuit_threshold == 5
    assert settings.rerank_circuit_open_seconds == 300


def test_rerank_settings_read_env(env):
    env.setenv("RETRIEVAL_RERANK_ENABLED", "true")
    env.setenv("RERANK_MODEL", "qwen3-rerank")
    env.setenv("RERANK_BASE_URL", "https://example.test/api/v1")
    env.setenv("RETRIEVAL_RERANK_CANDIDATES", "15")
    env.setenv("RETRIEVAL_RERANK_TOP_N", "5")
    env.setenv("RERANK_MAX_RETRIES", "0")
    settings = config.load_settings()
    assert settings.retrieval_rerank_enabled
    assert settings.rerank_model == "qwen3-rerank"
    assert settings.rerank_base_url == "https://example.test/api/v1"
    assert settings.retrieval_rerank_candidates == 15
    assert settings.retrieval_rerank_top_n == 5
    assert settings.rerank_max_retries == 0


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("RETRIEVAL_RERANK_ENABLED", "maybe"),
        ("RETRIEVAL_RERANK_CANDIDATES", "51"),
        ("RETRIEVAL_RERANK_TOP_N", "30"),
        ("RERANK_MAX_RETRIES", "-1"),
        ("RERANK_MODEL", ""),
    ],
)
def test_invalid_rerank_configuration_fails_at_startup(env, key, value):
    env.setenv(key, value)
    with pytest.raises(RuntimeError, match=key):
        config.load_settings()
