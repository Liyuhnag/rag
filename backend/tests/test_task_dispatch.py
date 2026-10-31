# -*- coding: utf-8 -*-
"""维护任务按持久化阶段补投。"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from model.document_task.model import DocumentTaskStage as Stage
from task import maintenance


@pytest.mark.parametrize("has_pending", [False, True])
def test_redispatch_recovers_before_finding_pending_tasks(monkeypatch, has_pending):
    calls = []
    recovered_at = []

    def recover(now, limit):
        recovered_at.append(now)
        assert limit == maintenance._REDISPATCH_BATCH
        calls.append("recover")
        return 1

    def find_pending(older_than, limit):
        assert len(recovered_at) == 1
        assert recovered_at[0] - older_than == timedelta(seconds=120)
        assert limit == maintenance._REDISPATCH_BATCH
        calls.append("find-pending")
        return [SimpleNamespace(id="task", stage=Stage.INDEXING)] if has_pending else []

    def dispatch(task_id, stage):
        calls.append((task_id, stage))

    monkeypatch.setattr(maintenance, "recover_tasks", recover)
    monkeypatch.setattr(maintenance.task_orm, "find_pending", find_pending)
    monkeypatch.setattr(maintenance, "dispatch_task", dispatch)
    maintenance.redispatch_pending_tasks()
    expected = ["recover", "find-pending"]
    if has_pending:
        expected.append(("task", Stage.INDEXING))
    assert calls == expected


def test_beat_recovers_every_minute_and_polls_mineru_independently():
    schedule = maintenance.celery_app.conf.beat_schedule
    assert schedule["redispatch-pending-tasks"] == {
        "task": "rag.redispatch_pending_tasks", "schedule": 60.0,
    }
    assert schedule["poll-parse-jobs"] == {
        "task": "rag.poll_parse_jobs", "schedule": 10.0,
    }
