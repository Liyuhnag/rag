# -*- coding: utf-8 -*-
"""外部存储确认、集合维度与版本清理条件。"""

from types import SimpleNamespace

import pytest

from config import Settings
from services.indexing.elasticsearch import ElasticsearchChunkStore
from services.indexing.milvus import MilvusChunkStore


def test_es_partial_bulk_failure_cannot_be_success():
    client = SimpleNamespace(
        bulk=lambda **kwargs: {
            "errors": True,
            "items": [{"index": {"status": 429}}],
        }
    )
    store = ElasticsearchChunkStore(Settings(), client=client, sleep=lambda _: None)
    with pytest.raises(RuntimeError, match="批量写入失败"):
        store.upsert([{"chunk_id": "child"}])


def test_es_uses_shared_child_id_and_keeps_parent_reference():
    calls = []

    def bulk(**kwargs):
        calls.append(kwargs)
        return {"errors": False, "items": [{"index": {"status": 201}}]}

    store = ElasticsearchChunkStore(
        Settings(es_index="test-index"), client=SimpleNamespace(bulk=bulk)
    )
    store.upsert([{"chunk_id": "child", "parent_id": "parent", "content": "正文"}])
    assert calls[0]["index"] == "test-index"
    assert calls[0]["operations"] == [
        {"index": {"_id": "child"}},
        {"chunk_id": "child", "parent_id": "parent", "content": "正文"},
    ]
    assert calls[0]["refresh"] is False


def test_es_retries_only_failed_bulk_items():
    calls = []
    waits = []

    def bulk(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"errors": True, "items": [
                {"index": {"status": 201}}, {"index": {"status": 429}}
            ]}
        return {"errors": False, "items": [{"index": {"status": 200}}]}

    store = ElasticsearchChunkStore(
        Settings(), client=SimpleNamespace(bulk=bulk), sleep=waits.append
    )
    store.upsert([{"chunk_id": "a"}, {"chunk_id": "b"}])
    assert waits == [1]
    assert calls[1]["operations"] == [
        {"index": {"_id": "b"}}, {"chunk_id": "b"}
    ]


@pytest.mark.parametrize(
    "result", [{"timed_out": True}, {"failures": ["failed"]}, {"version_conflicts": 1}]
)
def test_es_delete_failure_is_not_ignored(result):
    store = ElasticsearchChunkStore(
        Settings(), client=SimpleNamespace(delete_by_query=lambda **kwargs: result)
    )
    with pytest.raises(RuntimeError, match="清理失败"):
        store.delete_version("doc", 2)


def test_es_old_version_cleanup_never_deletes_newer_versions_or_other_documents():
    calls = []

    def delete(**kwargs):
        calls.append(kwargs)
        return {}

    store = ElasticsearchChunkStore(
        Settings(), client=SimpleNamespace(delete_by_query=delete)
    )
    store.prune_older("doc", 2)
    assert calls[0]["query"] == {
        "bool": {
            "filter": [
                {"term": {"document_id": "doc"}},
                {"range": {"version": {"lt": 2}}},
            ]
        }
    }


def test_milvus_dimension_mismatch_refuses_existing_collection():
    client = SimpleNamespace(
        has_collection=lambda name, **kwargs: True,
        describe_collection=lambda name, **kwargs: {
            "fields": [{"name": "vector", "params": {"dim": 8}}]
        },
    )
    store = MilvusChunkStore(Settings(embedding_dim=2), client=client)
    with pytest.raises(RuntimeError, match="维度不匹配"):
        store.ensure()


def test_milvus_upsert_must_confirm_every_child():
    store = MilvusChunkStore(
        Settings(),
        client=SimpleNamespace(upsert=lambda *args, **kwargs: {"upsert_count": 0}),
    )
    with pytest.raises(RuntimeError, match="未确认"):
        store.upsert([{"chunk_id": "child"}])


def test_milvus_cleanup_escapes_document_id_and_preserves_newer_versions():
    calls = []
    client = SimpleNamespace(delete=lambda *args, **kwargs: calls.append(kwargs))
    store = MilvusChunkStore(Settings(), client=client)
    store.prune_older('doc"quote', 2)
    assert calls == [
        {"filter": 'document_id == "doc\\"quote" and version < 2', "timeout": 60}
    ]


def test_milvus_new_collection_has_explicit_schema_and_cosine_index():
    state = {}

    def create(name, schema, index_params, **kwargs):
        state["schema"] = schema.to_dict()
        state["index"] = [item.to_dict() for item in index_params]

    def describe(name, **kwargs):
        return {
            "fields": state["schema"]["fields"],
            "description": state["schema"]["description"],
        }

    client = SimpleNamespace(
        has_collection=lambda name, **kwargs: False,
        create_collection=create,
        describe_collection=describe,
        load_collection=lambda name, **kwargs: None,
    )
    MilvusChunkStore(Settings(embedding_dim=2), client=client).ensure()
    fields = {field["name"]: field for field in state["schema"]["fields"]}
    assert fields["chunk_id"]["is_primary"]
    assert fields["vector"]["params"]["dim"] == 2
    assert "content" not in fields
    assert state["index"][0]["metric_type"] == "COSINE"


def test_milvus_same_dimension_different_model_requires_new_collection():
    client = SimpleNamespace(
        has_collection=lambda name, **kwargs: True,
        describe_collection=lambda name, **kwargs: {
            "description": "rag-child-embeddings:another-model",
            "fields": [{"name": "vector", "params": {"dim": 2}}],
        },
    )
    with pytest.raises(RuntimeError, match="模型不匹配"):
        MilvusChunkStore(Settings(embedding_dim=2), client=client).ensure()


def test_es_existing_keyword_content_mapping_is_rejected():
    indices = SimpleNamespace(
        exists=lambda **kwargs: True,
        get_mapping=lambda **kwargs: {
            "old": {"mappings": {"properties": {"content": {"type": "keyword"}}}}
        },
    )
    with pytest.raises(RuntimeError, match="mapping 不兼容"):
        ElasticsearchChunkStore(
            Settings(), client=SimpleNamespace(indices=indices)
        ).ensure()


def test_es_consistency_queries_are_scoped_to_document_version():
    calls = []

    def count(**kwargs):
        calls.append(kwargs)
        return {"count": 2}

    client = SimpleNamespace(
        count=count,
        mget=lambda **kwargs: {
            "docs": [
                {"_id": "a", "found": True, "_source": {"document_id": "doc", "version": 2}},
                {"_id": "b", "found": True, "_source": {"document_id": "other", "version": 2}},
            ]
        },
    )
    store = ElasticsearchChunkStore(Settings(), client=client)
    assert store.count_version("doc", 2) == 2
    assert calls[0]["query"]["bool"]["filter"] == [
        {"term": {"document_id": "doc"}}, {"term": {"version": 2}}
    ]
    assert not store.contains("doc", 2, ["a", "b"])


def test_milvus_consistency_queries_are_scoped_to_document_version():
    calls = []

    def query(*args, **kwargs):
        calls.append(kwargs)
        return [{"count(*)": 2}]

    client = SimpleNamespace(
        query=query,
        get=lambda *args, **kwargs: [
            {"chunk_id": "a", "document_id": "doc", "version": 2},
            {"chunk_id": "b", "document_id": "doc", "version": 1},
        ],
    )
    store = MilvusChunkStore(Settings(), client=client)
    assert store.count_version('doc"quote', 2) == 2
    assert calls[0]["filter"] == 'document_id == "doc\\"quote" and version == 2'
    assert not store.contains("doc", 2, ["a", "b"])
