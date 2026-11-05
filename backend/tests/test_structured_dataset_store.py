# -*- coding: utf-8 -*-
"""不连接数据库，验证版本锁、同版本替换及事务异常路径。"""

import json
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone

import pytest

from model.structured_dataset import orm
from model.structured_dataset.model import StructuredDataset


def dataset(sheet="a", version=1):
    return StructuredDataset(
        id=f"id-{sheet}-{version}",
        document_id="doc",
        version=version,
        sheet_name=sheet,
        file_type="csv",
        schema_json=[{"name": "a", "type": "integer", "nullable": False}],
        row_count=3,
        source_json_key="json",
        source_md_key="md",
    )


class MemoryDatabase:
    def __init__(self, version=1):
        self.version = version
        self.rows = []
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        before = list(self.rows)
        try:
            yield self
        except Exception:
            # Model the real transaction's rollback boundary, including extraction failures.
            self.rows = before
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def fetchone(self):
        return None if self.version is None else {"parse_version": self.version}

    def execute(self, sql, params):
        self.calls.append((sql, params))
        if "DELETE FROM" in sql:
            self.rows = [
                row
                for row in self.rows
                if (row["document_id"], row["version"])
                != (params["id"], params["version"])
            ]
        elif "INSERT INTO" in sql:
            self.rows.append(params)


@pytest.mark.parametrize("current_version", [None, 0, 2])
def test_version_guard_precedes_deletion_and_generator_consumption(
    monkeypatch, current_version
):
    database = MemoryDatabase(current_version)
    monkeypatch.setattr(orm, "transaction", database.transaction)

    def forbidden():
        raise AssertionError("must not read stale artifact")
        yield

    assert orm.replace_current_version("doc", 1, forbidden()) is False
    assert len(database.calls) == 1
    assert "FOR UPDATE" in database.calls[0][0]
    assert database.rows == []


def test_same_version_replace_is_atomic_and_preserves_other_versions(monkeypatch):
    database = MemoryDatabase()
    old_version = {"document_id": "doc", "version": 0, "id": "old"}
    database.rows.append(old_version)
    monkeypatch.setattr(orm, "transaction", database.transaction)
    assert orm.replace_current_version("doc", 1, [dataset(), dataset("b")])
    assert len(database.rows) == 3
    assert orm.replace_current_version("doc", 1, [dataset()])
    assert [row["id"] for row in database.rows] == ["old", "id-a-1"]
    row = database.rows[-1]
    assert json.loads(row["schema_json"]) == dataset().schema_json
    assert row["status"] == "SCHEMA_READY"
    assert row["storage_table"] is None
    assert row["source_json_key"] == "json"
    assert row["created_at"].tzinfo is None
    assert database.commits == 2
    assert database.rollbacks == 0
    assert all("UPDATE documents" not in sql for sql, _ in database.calls)


def test_extraction_failure_rolls_back_deleted_and_partially_inserted_schemas(
    monkeypatch,
):
    database = MemoryDatabase()
    monkeypatch.setattr(orm, "transaction", database.transaction)
    assert orm.replace_current_version("doc", 1, [dataset("original")])
    before = list(database.rows)
    closed = []

    def failing():
        try:
            yield dataset("replacement")
            raise ValueError("broken block")
        finally:
            closed.append(True)

    with pytest.raises(ValueError, match="broken block"):
        orm.replace_current_version("doc", 1, failing())
    assert database.rows == before
    assert database.rollbacks == 1
    assert closed == [True]


def test_mismatched_dataset_rejected_and_generator_closed(monkeypatch):
    database = MemoryDatabase()
    monkeypatch.setattr(orm, "transaction", database.transaction)
    closed = []

    def invalid():
        try:
            yield dataset(version=2)
        finally:
            closed.append(True)

    with pytest.raises(ValueError, match="版本不匹配"):
        orm.replace_current_version("doc", 1, invalid())
    assert closed == [True]
    assert database.rows == []
    assert database.rollbacks == 1


def test_list_by_version_decodes_json_and_timestamps(monkeypatch):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = {
        **dataset().__dict__,
        "schema_json": json.dumps(dataset().schema_json),
        "status": "SCHEMA_READY",
        "created_at": now,
        "updated_at": now,
    }
    calls = []

    def execute(sql, params):
        calls.append((sql, params))
        return [row]

    monkeypatch.setattr(orm, "execute", execute)
    result = orm.list_by_version("doc", 1)
    assert result[0].schema_json == dataset().schema_json
    assert result[0].created_at.tzinfo == timezone.utc
    assert result[0].storage_table is None
    assert calls[0][1] == {"id": "doc", "version": 1}
    assert "ORDER BY sheet_name" in calls[0][0]
