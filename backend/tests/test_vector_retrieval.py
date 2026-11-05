# -*- coding: utf-8 -*-
"""Milvus 子块向量检索（VectorRetriever）单元测试。"""

import pytest

from config import Settings
from services.retrieval import VectorRetrievalInput
from services.retrieval import VectorRetriever
from services.retrieval import build_milvus_filter
from services.retrieval import escape_milvus_string


class _FakeEmbedder:
    def __init__(self, dim: int = 4, fail: Exception | None = None):
        self.dim = dim
        self.fail = fail
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self.fail is not None:
            raise self.fail
        return [[0.5] * self.dim for _ in texts]


class _FakeMilvusClient:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_build_milvus_filter_escapes_injection_and_binds_versions():
    assert escape_milvus_string('a"b\\c') == 'a\\"b\\\\c'

    expr = build_milvus_filter(
        document_ids=('doc-"1"', "doc-2"),
        file_types=("PDF", "MD"),
    )
    assert (
        expr
        == 'document_id in ["doc-\\"1\\"", "doc-2"] and file_type in ["pdf", "md"]'
    )

    versioned = build_milvus_filter(
        document_ids=("d1", "d2"),
        document_versions={"d1": 2, "d2": 5},
    )
    assert (
        versioned
        == '((document_id == "d1" and version == 2) or (document_id == "d2" and version == 5))'
    )


def test_vector_retriever_orders_ties_deterministically_and_assigns_ranks():
    settings = Settings(embedding_dim=4, milvus_collection="document_chunks")
    embedder = _FakeEmbedder(dim=4)
    raw_batch = [
        [
            {
                "id": "c-2",
                "distance": 0.85,
                "entity": {
                    "chunk_id": "c-2",
                    "parent_id": "p-1",
                    "parent_index": 0,
                    "child_order": 2,
                    "document_id": "doc-1",
                    "version": 1,
                    "chunk_index": 2,
                    "file_type": "pdf",
                },
            },
            {
                "id": "c-1",
                "distance": 0.85,
                "entity": {
                    "chunk_id": "c-1",
                    "parent_id": "p-1",
                    "parent_index": 0,
                    "child_order": 1,
                    "document_id": "doc-1",
                    "version": 1,
                    "chunk_index": 1,
                    "file_type": "pdf",
                },
            },
            {
                "id": "c-0",
                "distance": 0.92,
                "entity": {
                    "chunk_id": "c-0",
                    "parent_id": "p-0",
                    "parent_index": 0,
                    "child_order": 0,
                    "document_id": "doc-1",
                    "version": 1,
                    "chunk_index": 0,
                    "file_type": "pdf",
                },
            },
        ]
    ]
    milvus = _FakeMilvusClient([raw_batch])
    retriever = VectorRetriever(settings, embedder=embedder, client=milvus)
    result = retriever.retrieve(
        VectorRetrievalInput(
            query="报销标准",
            document_ids=("doc-1",),
            file_types=("pdf",),
            top_k=10,
        )
    )
    assert not result.degraded
    assert [hit.chunk_id for hit in result.hits] == ["c-0", "c-1", "c-2"]
    assert [hit.rank for hit in result.hits] == [1, 2, 3]
    assert milvus.calls[0]["collection_name"] == "document_chunks"
    assert milvus.calls[0]["search_params"]["metric_type"] == "COSINE"


def test_vector_retriever_degrades_when_embedding_fails():
    settings = Settings(embedding_dim=4)
    embedder = _FakeEmbedder(dim=4, fail=RuntimeError("dashscope 429"))
    milvus = _FakeMilvusClient([])
    retriever = VectorRetriever(settings, embedder=embedder, client=milvus)

    result = retriever.retrieve(
        VectorRetrievalInput(query="报销标准", document_ids=("doc-1",))
    )
    assert result.degraded
    assert result.degraded_reason == "EMBEDDING_UNAVAILABLE"
    assert result.hits == ()
    assert milvus.calls == []


@pytest.mark.parametrize("recover_on_retry", [True, False])
def test_vector_retriever_retries_milvus_once_and_degrades_if_still_failing(
    recover_on_retry,
):
    settings = Settings(embedding_dim=4)
    embedder = _FakeEmbedder(dim=4)
    responses = [RuntimeError("milvus timeout")]
    if recover_on_retry:
        responses.append(
            [
                [
                    {
                        "id": "c-1",
                        "distance": 0.9,
                        "entity": {
                            "chunk_id": "c-1",
                            "parent_id": "p-1",
                            "parent_index": 0,
                            "child_order": 0,
                            "document_id": "doc-1",
                            "version": 1,
                            "chunk_index": 0,
                            "file_type": "pdf",
                        },
                    }
                ]
            ]
        )
    else:
        responses.append(RuntimeError("milvus still down"))

    milvus = _FakeMilvusClient(responses)
    retriever = VectorRetriever(
        settings, embedder=embedder, client=milvus, sleep=lambda _: None
    )
    result = retriever.retrieve(
        VectorRetrievalInput(query="报销标准", document_ids=("doc-1",))
    )
    assert len(milvus.calls) == 2
    if recover_on_retry:
        assert not result.degraded
        assert len(result.hits) == 1
    else:
        assert result.degraded
        assert result.degraded_reason == "VECTOR_UNAVAILABLE"
        assert result.hits == ()
