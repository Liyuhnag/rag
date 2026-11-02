# -*- coding: utf-8 -*-
"""Elasticsearch 子块 BM25 关键词检索（KeywordRetriever）单元测试。"""

import pytest

from config import Settings
from services.retrieval import KeywordRetrievalInput
from services.retrieval import KeywordRetriever
from services.retrieval import PageRange
from services.retrieval import build_es_dsl


class _FakeESClient:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_build_es_dsl_includes_filters_page_overlap_and_highlight():
    req = KeywordRetrievalInput(
        query="  差旅费   住宿标准  ",
        document_ids=("doc-1", "doc-2"),
        file_types=("PDF", "DOCX"),
        page_range=PageRange(3, 10),
        extracted_keywords=("第三章",),
        include_highlight=True,
        top_k=25,
    )
    dsl = build_es_dsl(req)
    assert dsl["size"] == 25
    assert dsl["track_total_hits"] is False
    filters = dsl["query"]["bool"]["filter"]
    assert {"terms": {"chunk_level": ["CHILD", "child"]}} in filters
    assert {"terms": {"document_id": ["doc-1", "doc-2"]}} in filters
    assert {"terms": {"file_type": ["pdf", "docx"]}} in filters
    assert {"range": {"page_end": {"gte": 3}}} in filters
    assert {"range": {"page_start": {"lte": 10}}} in filters
    assert "highlight" in dsl

    # 关闭高亮时不应包含 highlight 字段
    no_hl = build_es_dsl(
        KeywordRetrievalInput(
            query="差旅费",
            document_ids=("doc-1",),
            include_highlight=False,
        )
    )
    assert "highlight" not in no_hl


def test_keyword_retriever_parses_highlights_and_orders_ties():
    settings = Settings(es_index="document_chunks")
    es_response = {
        "hits": {
            "hits": [
                {
                    "_id": "c-2",
                    "_score": 8.5,
                    "_source": {
                        "chunk_id": "c-2",
                        "parent_id": "p-1",
                        "parent_index": 0,
                        "child_order": 2,
                        "document_id": "doc-1",
                        "version": 1,
                        "chunk_index": 2,
                        "file_type": "pdf",
                        "content": "一线城市住宿标准为500元",
                        "heading_path": "第三章 > 3.2",
                        "locator": "page:5",
                        "page_start": 5,
                        "page_end": 5,
                    },
                    "highlight": {
                        "content": ["一线城市<em>住宿标准</em>", "为<em>500元</em>"]
                    },
                },
                {
                    "_id": "c-1",
                    "_score": 8.5,
                    "_source": {
                        "chunk_id": "c-1",
                        "parent_id": "p-1",
                        "parent_index": 0,
                        "child_order": 1,
                        "document_id": "doc-1",
                        "version": 1,
                        "chunk_index": 1,
                        "file_type": "pdf",
                        "content": "出差审批流程",
                        "heading_path": "第三章 > 3.1",
                        "locator": "page:4",
                        "page_start": 4,
                        "page_end": 4,
                    },
                },
            ]
        }
    }
    client = _FakeESClient([es_response])
    retriever = KeywordRetriever(settings, client=client)
    result = retriever.retrieve(
        KeywordRetrievalInput(query="住宿标准 500元", document_ids=("doc-1",))
    )
    assert not result.degraded
    assert [hit.chunk_id for hit in result.hits] == ["c-1", "c-2"]
    assert [hit.rank for hit in result.hits] == [1, 2]
    assert result.hits[1].highlight == "一线城市<em>住宿标准</em>……为<em>500元</em>"
    assert client.calls[0]["index"] == "document_chunks"


@pytest.mark.parametrize("recover_on_retry", [True, False])
def test_keyword_retriever_retries_once_and_degrades_on_failure(recover_on_retry):
    settings = Settings(es_index="document_chunks")
    responses = [RuntimeError("es connection timeout")]
    if recover_on_retry:
        responses.append(
            {
                "hits": {
                    "hits": [
                        {
                            "_id": "c-1",
                            "_score": 10.0,
                            "_source": {
                                "chunk_id": "c-1",
                                "parent_id": "p-1",
                                "parent_index": 0,
                                "child_order": 0,
                                "document_id": "doc-1",
                                "version": 1,
                                "chunk_index": 0,
                                "file_type": "pdf",
                                "content": "正文",
                                "heading_path": "一",
                                "locator": "p1",
                            },
                        }
                    ]
                }
            }
        )
    else:
        responses.append(RuntimeError("es cluster down"))

    client = _FakeESClient(responses)
    retriever = KeywordRetriever(settings, client=client, sleep=lambda _: None)
    result = retriever.retrieve(
        KeywordRetrievalInput(query="正文", document_ids=("doc-1",))
    )
    assert len(client.calls) == 2
    if recover_on_retry:
        assert not result.degraded
        assert len(result.hits) == 1
    else:
        assert result.degraded
        assert result.degraded_reason == "KEYWORD_UNAVAILABLE"
        assert result.hits == ()
