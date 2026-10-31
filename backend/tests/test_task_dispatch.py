# -*- coding: utf-8 -*-
"""维护任务按持久化阶段补投。"""

from types import SimpleNamespace

from model.document_task.model import DocumentTaskStage as Stage
from task import maintenance


def test_redispatch_pending_tasks(monkeypatch):
    calls = []
    monkeypatch.setattr(maintenance.task_orm, "find_pending", lambda *args: [SimpleNamespace(id="task", stage=Stage.INDEXING)])
    monkeypatch.setattr(maintenance.task_orm, "recover_chunking", lambda *args: [SimpleNamespace(id="recovered", stage=Stage.CHUNKING)])
    monkeypatch.setattr(maintenance, "dispatch_task", lambda task_id, stage: calls.append((task_id, stage)))
    maintenance.redispatch_pending_tasks()
    assert calls == [("recovered", Stage.CHUNKING), ("task", Stage.INDEXING)]
