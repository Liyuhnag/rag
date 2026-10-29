# -*- coding: utf-8 -*-
"""索引版本锁、计数和 READY 提交边界。"""

from contextlib import contextmanager

import pytest

from model.document_chunk import orm


class Cursor:
    def __init__(self, version=2):
        self.version = version
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return {"parse_version": self.version, "chunk_count": 1}


def install_transaction(monkeypatch, cursor):
    events = []

    @contextmanager
    def transaction():
        try:
            yield type("Connection", (), {"cursor": lambda self: cursor})()
        except Exception:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    monkeypatch.setattr(orm, "transaction", transaction)
    return events


def test_stale_indexing_locks_and_returns_without_reading_chunks(monkeypatch):
    cursor = Cursor(version=3)
    events = install_transaction(monkeypatch, cursor)
    with orm.index_current_version("doc", 2) as session:
        assert session is None
    assert len(cursor.calls) == 1
    assert "FOR UPDATE" in cursor.calls[0][0]
    assert events == ["commit"]


def test_embedding_metadata_and_ready_are_committed_for_current_version(monkeypatch):
    cursor = Cursor()
    events = install_transaction(monkeypatch, cursor)
    with orm.index_current_version("doc", 2) as session:
        assert "READY" not in cursor.calls[0][0]
        session.finish("model", 1)
    assert events == ["commit"]
    assert "embedding_model" in cursor.calls[-3][0]
    assert "version < %(version)s" in cursor.calls[-2][0]
    assert "status = 'READY'" in cursor.calls[-1][0]
    assert cursor.calls[-1][1]["version"] == 2


def test_count_mismatch_rolls_back_without_marking_ready(monkeypatch):
    cursor = Cursor()
    events = install_transaction(monkeypatch, cursor)
    with pytest.raises(RuntimeError, match="计数不一致"):
        with orm.index_current_version("doc", 2) as session:
            session.finish("model", 2)
    assert events == ["rollback"]
    assert len(cursor.calls) == 1
