# -*- coding: utf-8 -*-
"""索引队列的成功、失败与过期任务。"""

import importlib
from types import SimpleNamespace

import pytest

from model.document_task.model import DocumentTaskStage
from model.document_task.model import DocumentTaskStatus

worker = importlib.import_module("task.index_document")


@pytest.mark.parametrize("outcome", [True, False, RuntimeError("Milvus 不可用")])
def test_indexing_task_completes_only_after_success(monkeypatch, outcome):
    calls = []
    task = SimpleNamespace(
        id="task",
        document_id="doc",
        version=2,
        status=DocumentTaskStatus.RUNNING,
        stage=DocumentTaskStage.INDEXING,
    )
    monkeypatch.setattr(worker.task_orm, "get", lambda task_id: task)
    monkeypatch.setattr(
        worker.document_orm,
        "get",
        lambda doc_id: SimpleNamespace(id="doc", parse_version=2),
    )

    class Service:
        def run(self, document, version, heartbeat):
            heartbeat()
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(worker, "DocumentIndexingService", Service)
    monkeypatch.setattr(
        worker.task_orm, "update_progress", lambda *args: calls.append("heartbeat")
    )
    monkeypatch.setattr(
        worker.task_orm, "complete", lambda *args: calls.append("complete")
    )
    monkeypatch.setattr(worker.task_orm, "fail", lambda *args: calls.append("fail"))
    monkeypatch.setattr(
        worker.document_orm, "mark_failed", lambda *args, **kwargs: calls.append(kwargs)
    )
    if isinstance(outcome, Exception):
        with pytest.raises(RuntimeError, match="Milvus"):
            worker.index_document.run("task")
        assert calls == ["heartbeat", "fail", {"expected_version": 2}]
    else:
        worker.index_document.run("task")
        assert calls == ["heartbeat"] + (["complete"] if outcome else [])


def test_stale_index_task_does_not_call_service(monkeypatch):
    task = SimpleNamespace(
        document_id="doc",
        version=1,
        status=DocumentTaskStatus.RUNNING,
        stage=DocumentTaskStage.INDEXING,
    )
    monkeypatch.setattr(worker.task_orm, "get", lambda task_id: task)
    monkeypatch.setattr(
        worker.document_orm, "get", lambda doc_id: SimpleNamespace(parse_version=2)
    )
    monkeypatch.setattr(
        worker, "DocumentIndexingService", lambda: pytest.fail("过期任务不能索引")
    )
    worker.index_document.run("task")


def test_finished_parse_dispatches_index_queue(monkeypatch):
    from services.indexing.flow import enqueue_indexing

    calls = []
    monkeypatch.setattr(
        worker.task_orm, "update_progress", lambda *args: calls.append(args)
    )
    monkeypatch.setattr(
        worker.index_document, "apply_async", lambda **kwargs: calls.append(kwargs)
    )
    enqueue_indexing("task")
    assert calls[0][1] == DocumentTaskStage.INDEXING
    assert calls[1] == {"args": ["task"], "queue": "index"}
