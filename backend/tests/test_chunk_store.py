# -*- coding: utf-8 -*-
"""当前版本锁阻止过期切分覆盖。"""

from contextlib import contextmanager

from model.document_chunk import orm
from model.document_chunk.model import ChunkLevel
from services.chunking.common.models import ChunkOptions
from services.chunking.token import TokenChunker
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument


def test_current_version_batches_parent_before_child_and_updates_counts(monkeypatch):
    inserted = []
    updates = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            updates.append((sql, params))

        def fetchone(self):
            return {"parse_version": 1}

        def executemany(self, sql, rows):
            inserted.extend(rows)

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def transaction():
        yield Connection()

    parsed = ParsedDocument(
        ParseRequest("doc", 1, "source", "txt", "a.txt", 4),
        "a",
        [ParsedBlock("text", "一段正文")],
    )
    monkeypatch.setattr(orm, "transaction", transaction)
    result = orm.replace_current_version(
        "doc",
        1,
        TokenChunker().iter_split(parsed, ChunkOptions()),
    )
    assert result is True
    assert [row["chunk_level"] for row in inserted] == [
        ChunkLevel.PARENT.value,
        ChunkLevel.CHILD.value,
    ]
    assert updates[-1][1]["parents"] == 1
    assert updates[-1][1]["children"] == 1
    assert "READY" not in updates[-1][0]


def test_stale_version_does_not_consume_or_delete_chunks(monkeypatch):
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            calls.append(sql)

        def fetchone(self):
            return {"parse_version": 2}

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def transaction():
        yield Connection()

    def chunks():
        raise AssertionError("过期版本不应读取块")
        yield

    monkeypatch.setattr(orm, "transaction", transaction)
    assert orm.replace_current_version("doc", 1, chunks()) is False
    assert len(calls) == 1
    assert "FOR UPDATE" in calls[0]
