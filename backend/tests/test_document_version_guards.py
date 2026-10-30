# -*- coding: utf-8 -*-
"""文档版本条件写入，SQL 边界使用 fake execute，不连接数据库。"""

from datetime import datetime
from datetime import timezone

import pytest

from model.document import orm


@pytest.fixture
def executed(monkeypatch):
    calls = []

    def fake_execute(sql, params):
        calls.append((" ".join(sql.split()), params))
        return []

    monkeypatch.setattr(orm, "execute", fake_execute)
    return calls


def test_set_parsed_artifacts_requires_expected_version(executed):
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)

    orm.set_parsed_artifacts(
        "doc-1", "parsed/3.md", "parsed/3.json", now, expected_version=3
    )

    sql, params = executed[0]
    assert "WHERE id = %(id)s AND parse_version = %(expected_version)s" in sql
    assert params == {
        "id": "doc-1",
        "parsed_md_key": "parsed/3.md",
        "parsed_json_key": "parsed/3.json",
        "now": datetime(2026, 10, 27),
        "expected_version": 3,
    }


@pytest.mark.parametrize("operation", ["mark_processing", "begin_parse"])
def test_mark_processing_updates_only_the_current_version(monkeypatch, operation):
    calls = []

    def execute_count(sql, params):
        calls.append((" ".join(sql.split()), params, "count"))
        return 1

    def execute(sql, params):
        raise AssertionError("已改到 PROCESSING 时不必再读")

    monkeypatch.setattr(orm, "execute_count", execute_count)
    monkeypatch.setattr(orm, "execute", execute)
    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    assert getattr(orm, operation)("doc-1", 2, now)
    sql, params, _kind = calls[0]
    assert "status = 'PROCESSING'" in sql
    assert "parse_version = %(version)s" in sql
    assert params["id"] == "doc-1"
    assert params["version"] == 2
    assert params["now"] == datetime(2026, 10, 30)


@pytest.mark.parametrize("operation", ["mark_processing", "begin_parse"])
def test_mark_processing_accepts_a_version_that_is_already_processing(
    monkeypatch, operation
):
    def execute_count(sql, params):
        return 0

    def execute(sql, params):
        return [{"status": "PROCESSING"}]

    monkeypatch.setattr(orm, "execute_count", execute_count)
    monkeypatch.setattr(orm, "execute", execute)
    assert getattr(orm, operation)(
        "doc-1", 2, datetime(2026, 10, 30, tzinfo=timezone.utc)
    )


@pytest.mark.parametrize("operation", ["mark_processing", "begin_parse"])
def test_mark_processing_rejects_a_different_version(monkeypatch, operation):
    monkeypatch.setattr(orm, "execute_count", lambda sql, params: 0)
    monkeypatch.setattr(orm, "execute", lambda sql, params: [])
    assert getattr(orm, operation)(
        "doc-1", 2, datetime(2026, 10, 30, tzinfo=timezone.utc)
    ) is False


def test_mark_failed_requires_expected_version_and_truncates_message(executed):
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)

    orm.mark_failed("doc-1", "x" * 1100, now, expected_version=0)

    sql, params = executed[0]
    assert "WHERE id = %(id)s AND parse_version = %(expected_version)s" in sql
    assert params == {
        "id": "doc-1",
        "message": "x" * 1024,
        "now": datetime(2026, 10, 27),
        "expected_version": 0,
    }


@pytest.mark.parametrize("explicit_none", [False, True])
@pytest.mark.parametrize("operation", ["artifacts", "failed"])
def test_legacy_calls_do_not_require_version(executed, explicit_none, operation):
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    options = {"expected_version": None} if explicit_none else {}

    if operation == "artifacts":
        orm.set_parsed_artifacts("doc-1", None, None, now, **options)
    else:
        orm.mark_failed("doc-1", "failed", now, **options)

    sql, params = executed[0]
    assert sql.endswith("WHERE id = %(id)s")
    assert "parse_version" not in sql
    assert "expected_version" not in params
    assert params["id"] == "doc-1"


@pytest.mark.parametrize("operation", ["artifacts", "failed"])
def test_expected_version_is_keyword_only(executed, operation):
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)

    with pytest.raises(TypeError):
        if operation == "artifacts":
            orm.set_parsed_artifacts("doc-1", None, None, now, 3)
        else:
            orm.mark_failed("doc-1", "failed", now, 3)

    assert executed == []
