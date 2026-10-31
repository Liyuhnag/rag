# -*- coding: utf-8 -*-
"""公共恢复的策略读取、空输入和投递失败边界。"""

from dataclasses import replace
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from types import SimpleNamespace

import pytest

from model.document_task import orm
from model.document_task.model import DocumentTaskStage as Stage
from services.task_runtime import lifecycle


@pytest.mark.parametrize("limit", [0, -1, -100])
def test_nonpositive_recovery_limit_does_not_access_database(monkeypatch, limit):
    def unexpected_query(*args):
        pytest.fail("非正数批次不应访问数据库")

    monkeypatch.setattr(orm, "execute", unexpected_query)
    monkeypatch.setattr(orm, "execute_count", unexpected_query)
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    assert orm.recover_stale({Stage.CHUNKING: now}, now, limit) == []


def test_empty_recovery_policy_does_not_access_database(monkeypatch):
    def unexpected_query(*args):
        pytest.fail("空策略不应访问数据库")

    monkeypatch.setattr(orm, "execute", unexpected_query)
    monkeypatch.setattr(orm, "execute_count", unexpected_query)
    assert orm.recover_stale({}, datetime.now(timezone.utc), 10) == []


def test_recovery_reads_current_policy_and_does_not_dispatch_empty_result(monkeypatch):
    now = datetime(2026, 10, 31, tzinfo=timezone(timedelta(hours=8)))
    policies = dict(lifecycle.POLICIES)
    policies[Stage.PARSING] = replace(policies[Stage.PARSING], idle_seconds=1234)
    monkeypatch.setattr(lifecycle, "POLICIES", policies)

    def recover(cutoffs, recovery_now, limit):
        assert cutoffs == {
            Stage.PARSING: now - timedelta(seconds=1234),
            Stage.MERGING: now - timedelta(seconds=600),
            Stage.CHUNKING: now - timedelta(seconds=600),
            Stage.INDEXING: now - timedelta(seconds=600),
        }
        assert recovery_now == now
        assert limit == 7
        return []

    def unexpected_dispatch(*args):
        pytest.fail("没有恢复成功的任务时不应投递")

    monkeypatch.setattr(orm, "recover_stale", recover)
    monkeypatch.setattr(lifecycle, "dispatch", unexpected_dispatch)
    assert lifecycle.recover(now, 7) == 0


def test_failed_dispatch_does_not_prevent_remaining_recovered_tasks(monkeypatch):
    tasks = [
        SimpleNamespace(id="parse", stage=Stage.PARSING),
        SimpleNamespace(id="index", stage=Stage.INDEXING),
    ]
    messages = []

    def recover(*args):
        return tasks

    def dispatch(task_id, stage):
        messages.append((task_id, stage))
        return task_id != "parse"

    monkeypatch.setattr(orm, "recover_stale", recover)
    monkeypatch.setattr(lifecycle, "dispatch", dispatch)
    assert lifecycle.recover(datetime.now(timezone.utc), 10) == 2
    assert messages == [("parse", Stage.PARSING), ("index", Stage.INDEXING)]
