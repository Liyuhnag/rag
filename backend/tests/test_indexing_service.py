# -*- coding: utf-8 -*-
"""双索引成功、失败重放和版本隔离。"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from model.document_chunk.model import ChunkLevel
from model.document_chunk.model import DocumentChunk
from services.indexing.service import DocumentIndexingService


def child(index=0):
    return DocumentChunk(
        id=f"child-{index}",
        document_id="doc",
        version=2,
        chunk_level=ChunkLevel.CHILD,
        parent_id="parent",
        parent_index=0,
        child_order=index,
        chunk_index=index,
        child_count=0,
        file_type="txt",
        page_start=None,
        page_end=None,
        locator="",
        heading_path="章节",
        content=f"正文{index}",
        token_count=3,
    )


class Store:
    def __init__(self, chunks=None, stale=False):
        self.chunks = [child(0), child(1)] if chunks is None else chunks
        self.stale = stale
        self.ready = False
        self.expected_count = len(self.chunks)

    @contextmanager
    def index_current_version(self, document_id, version):
        yield None if self.stale else self

    def iter_children(self, batch_size):
        for start in range(0, len(self.chunks), batch_size):
            yield self.chunks[start : start + batch_size]

    def validate_children(self):
        pass

    def finish(self, model, count):
        assert count == len(self.chunks)
        self.ready = True


class Sink:
    def __init__(self):
        self.rows = {"old": {"document_id": "doc", "version": 1}}
        self.fail = False
        self.missing = False
        self.batches = []

    def ensure(self):
        pass

    def delete_version(self, document_id, version):
        self.rows = {
            key: row for key, row in self.rows.items() if row["version"] != version
        }

    def upsert(self, rows):
        if self.fail:
            raise RuntimeError("存储不可用")
        self.batches.append([row["chunk_id"] for row in rows])
        self.rows.update({row["chunk_id"]: row for row in rows})

    def flush(self):
        pass

    def count_version(self, document_id, version):
        return sum(
            row["document_id"] == document_id and row["version"] == version
            for row in self.rows.values()
        ) - self.missing

    def contains(self, document_id, version, ids):
        return all(
            key in self.rows
            and self.rows[key]["document_id"] == document_id
            and self.rows[key]["version"] == version
            for key in ids
        )

    def prune_older(self, document_id, version):
        self.rows = {
            key: row for key, row in self.rows.items() if row["version"] >= version
        }


class Embedder:
    def __init__(self):
        self.texts = []

    def embed(self, texts):
        self.texts.extend(texts)
        return [[1.0, 0.0] for text in texts]


def setup_service(monkeypatch, store):
    monkeypatch.setattr(
        "services.indexing.service.chunk_orm.index_current_version",
        store.index_current_version,
    )
    settings = SimpleNamespace(
        embedding_model="test", embedding_dim=2, indexing_batch_size=1,
        es_bulk_size=1,
    )
    embedding = Embedder()
    vectors = Sink()
    keywords = Sink()
    service = DocumentIndexingService(settings, embedding, vectors, keywords)
    return service, embedding, vectors, keywords


def test_same_children_in_both_indexes_and_retry_is_idempotent(monkeypatch):
    store = Store()
    service, embedding, vectors, keywords = setup_service(monkeypatch, store)
    document = SimpleNamespace(id="doc", name="示例.txt")
    assert service.run(document, 2)
    assert service.run(document, 2)
    assert store.ready
    assert vectors.rows.keys() == keywords.rows.keys() == {"child-0", "child-1"}
    assert vectors.rows["child-0"]["parent_id"] == "parent"
    assert vectors.rows["child-0"]["vector"] == [1.0, 0.0]
    assert keywords.rows["child-0"]["content"] == "正文0"
    assert keywords.rows["child-0"]["document_name"] == "示例.txt"
    assert "content" not in vectors.rows["child-0"]
    assert embedding.texts == ["章节\n正文0", "章节\n正文1"] * 2


def test_partial_failure_never_marks_ready_and_can_replay(monkeypatch):
    store = Store()
    service, _, vectors, keywords = setup_service(monkeypatch, store)
    keywords.fail = True
    with pytest.raises(RuntimeError, match="存储不可用"):
        service.run(SimpleNamespace(id="doc", name="a.txt"), 2)
    assert not store.ready
    assert "old" in vectors.rows
    keywords.fail = False
    assert service.run(SimpleNamespace(id="doc", name="a.txt"), 2)
    assert store.ready
    assert vectors.rows.keys() == keywords.rows.keys() == {"child-0", "child-1"}


def test_stale_version_does_not_touch_external_indexes(monkeypatch):
    service, embedding, vectors, keywords = setup_service(
        monkeypatch, Store(stale=True)
    )
    assert not service.run(SimpleNamespace(id="doc", name="a"), 1)
    assert not embedding.texts
    assert vectors.rows.keys() == keywords.rows.keys() == {"old"}


def test_empty_chunks_cannot_be_ready(monkeypatch):
    store = Store(chunks=[])
    service, _, _, _ = setup_service(monkeypatch, store)
    with pytest.raises(RuntimeError, match="子块"):
        service.run(SimpleNamespace(id="doc", name="a"), 2)
    assert not store.ready


def test_external_count_mismatch_prevents_ready(monkeypatch):
    store = Store()
    service, _, vectors, _ = setup_service(monkeypatch, store)
    vectors.missing = True
    with pytest.raises(RuntimeError, match="不一致"):
        service.run(SimpleNamespace(id="doc", name="a"), 2)
    assert not store.ready
    assert "old" in vectors.rows


def test_index_progress_reports_confirmed_batches(monkeypatch):
    store = Store()
    service, _, _, _ = setup_service(monkeypatch, store)
    progress = []
    service.run(
        SimpleNamespace(id="doc", name="a"),
        2,
        heartbeat=lambda *args: progress.append(args),
    )
    assert progress == [(1, 2, "child-0"), (2, 2, "child-1")]


def test_es_bulk_size_is_independent_of_embedding_batches(monkeypatch):
    store = Store()
    service, _, _, keywords = setup_service(monkeypatch, store)
    service.settings.es_bulk_size = 2
    leases = []
    progress = []
    service.run(
        SimpleNamespace(id="doc", name="a"), 2,
        heartbeat=lambda *args: progress.append(args),
        lease=lambda: leases.append("renewed"),
    )
    assert keywords.batches == [["child-0", "child-1"]]
    assert leases == ["renewed", "renewed"]
    assert progress == [(2, 2, "child-1")]


def test_read_only_audit_detects_missing_index_without_writing(monkeypatch):
    store = Store()
    service, embedding, vectors, keywords = setup_service(monkeypatch, store)
    document = SimpleNamespace(id="doc", name="a")
    service.run(document, 2)
    embedded = list(embedding.texts)
    vector_batches = list(vectors.batches)
    keyword_batches = list(keywords.batches)
    assert service.audit(document, 2)
    assert embedding.texts == embedded
    assert vectors.batches == vector_batches
    assert keywords.batches == keyword_batches
    del keywords.rows["child-0"]
    with pytest.raises(RuntimeError, match="不一致"):
        service.audit(document, 2)
