# -*- coding: utf-8 -*-
"""失联切分任务的条件恢复。"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from model.document_task import orm
from model.document_task.model import DocumentTaskStatus


def test_recovery_returns_only_conditionally_reset_tasks(monkeypatch):
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    cutoff = now - timedelta(minutes=10)
    row = {
        "document_id": "doc-1",
        "version": 1,
        "status": "RUNNING",
        "stage": "CHUNKING",
        "progress": 85,
        "step": "文档切分中",
        "error_message": None,
    }
    queries = []

    def select(sql, params):
        queries.append((sql, params))
        return [dict(row, id="expired"), dict(row, id="finished-concurrently")]

    def update(sql, params):
        queries.append((sql, params))
        return int(params["id"] == "expired")

    monkeypatch.setattr(orm, "execute", select)
    monkeypatch.setattr(orm, "execute_count", update)

    recovered = orm.recover_chunking(cutoff, now, 100)

    assert [task.id for task in recovered] == ["expired"]
    assert recovered[0].status == DocumentTaskStatus.PENDING
    for sql, params in queries:
        assert "status = 'RUNNING'" in sql
        assert "stage = 'CHUNKING'" in sql
        assert "updated_at < %(older_than)s" in sql
        assert params["older_than"] == cutoff.replace(tzinfo=None)
    assert "status = 'PENDING'" in queries[1][0]
    assert queries[1][1]["now"] == now.replace(tzinfo=None)
