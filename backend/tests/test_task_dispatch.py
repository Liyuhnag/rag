# -*- coding: utf-8 -*-
"""解析任务投递和补投。"""

from types import SimpleNamespace

from services.upload import task_dispatch
from task import maintenance


def test_dispatch_task_success(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        task_dispatch.parse_document,
        "delay",
        lambda task_id: calls.append(task_id),
    )

    assert task_dispatch.dispatch_task("task-1") is True
    assert calls == ["task-1"]


def test_dispatch_task_swallows_broker_error(monkeypatch):
    def boom(_task_id: str) -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr(task_dispatch.parse_document, "delay", boom)

    assert task_dispatch.dispatch_task("task-2") is False


def test_redispatch_pending_tasks(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        maintenance.task_orm,
        "find_pending",
        lambda _older_than, _limit: [SimpleNamespace(id="task-3")],
    )
    monkeypatch.setattr(maintenance, "dispatch_task", calls.append)

    maintenance.redispatch_pending_tasks()

    assert calls == ["task-3"]
