# -*- coding: utf-8 -*-
"""Embedding 外部接口的顺序、重试及失败边界。"""

import httpx
import pytest

from config import Settings
from services.indexing.embedding import DashScopeEmbedder


def embedder(handler, **kwargs):
    return DashScopeEmbedder(
        Settings(dashscope_api_key="test", embedding_dim=2),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_embedding_restores_input_order_and_sends_configured_model():
    import json

    def handler(request):
        assert request.headers["authorization"] == "Bearer test"
        assert request.url.path == "/compatible-mode/v1/embeddings"
        assert json.loads(request.content) == {
            "model": "text-embedding-v4",
            "input": ["一", "二"],
            "dimensions": 2,
            "encoding_format": "float",
        }
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0, 1]},
                    {"index": 0, "embedding": [1, 0]},
                ]
            },
        )

    assert embedder(handler).embed(["一", "二"]) == [[1, 0], [0, 1]]


@pytest.mark.parametrize("status", [429, 503])
def test_transient_embedding_failure_retries_with_backoff(status):
    calls = []
    waits = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(status)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})

    assert embedder(handler, sleep=waits.append).embed(["一"]) == [[1, 0]]
    assert waits == [1, 2]


def test_auth_error_fails_immediately_without_echoing_response_secrets():
    waits = []
    provider = embedder(
        lambda request: httpx.Response(401, text="sensitive"), sleep=waits.append
    )
    with pytest.raises(RuntimeError, match="401") as error:
        provider.embed(["一"])
    assert "sensitive" not in str(error.value)
    assert not waits


@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"index": 1, "embedding": [1, 0]}],
        [{"index": 0, "embedding": [1]}],
        [{"index": 0, "embedding": [0, 0]}],
        [{"index": 0, "embedding": ["bad", 0]}],
    ],
)
def test_invalid_vectors_are_rejected_before_writing(data):
    provider = embedder(lambda request: httpx.Response(200, json={"data": data}))
    with pytest.raises(RuntimeError, match="无效"):
        provider.embed(["一"])


def test_missing_api_key_is_actionable():
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        DashScopeEmbedder(Settings())
