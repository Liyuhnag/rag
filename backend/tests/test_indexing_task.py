# -*- coding: utf-8 -*-
"""索引队列的成功、失败与过期任务。"""

import importlib
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace

import pytest

from model.document_task.model import DocumentTaskStage
from model.document_task.model import DocumentTaskStatus

worker = importlib.import_module("task.index_document")
audit_worker = importlib.import_module("task.audit_index")


def test_audit_task_only_checks_ready_documents(monkeypatch):
    from model.document.model import DocumentStatus

    document = SimpleNamespace(id="doc", status=DocumentStatus.FAILED, parse_version=2)
    monkeypatch.setattr(audit_worker.document_orm, "get", lambda _id: document)
    calls = []

    class Service:
        def audit(self, record, version):
            calls.append((record.id, version))
            return True

    monkeypatch.setattr(audit_worker, "DocumentIndexingService", Service)
    assert not audit_worker.audit_document_index.run("doc")
    document.status = DocumentStatus.READY
    assert audit_worker.audit_document_index.run("doc")
    assert calls == [("doc", 2)]


@pytest.mark.parametrize("parse_version", [2, 3])
def test_retry_failed_indexing_requeues_only_current_version(monkeypatch, parse_version):
    from model.document_task import orm as task_orm

    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            statements.append((sql, params))

        def fetchone(self):
            return {
                "status": "FAILED", "stage": "INDEXING", "version": 2,
                "parse_version": parse_version, "document_status": "FAILED",
            }

    @contextmanager
    def transaction():
        yield SimpleNamespace(cursor=lambda: Cursor())

    monkeypatch.setattr(task_orm, "transaction", transaction)
    assert task_orm.retry_failed_indexing("task", datetime.now(timezone.utc)) is (parse_version == 2)
    assert "FOR UPDATE" in statements[0][0]
    if parse_version == 2:
        assert "status = 'PROCESSING'" in statements[1][0]
        assert "status = 'PENDING'" in statements[2][0]
    else:
        assert len(statements) == 1


def test_retry_endpoint_accepts_failed_index_and_rejects_other_tasks(client, monkeypatch):
    task_router = importlib.import_module("api.tasks.router")

    task = SimpleNamespace(
        id="task", document_id="doc", version=2, status="FAILED",
        stage="INDEXING", progress=90, step="索引失败", error_message="错误",
    )
    calls = []

    def requeue(task_id, now):
        calls.append((task_id, now))
        task.status = "PENDING"
        task.error_message = None
        return True

    monkeypatch.setattr(task_router.document_task_orm, "get", lambda _id: task)
    monkeypatch.setattr(task_router.document_task_orm, "retry_failed_indexing", requeue)
    monkeypatch.setattr(task_router, "dispatch_task", calls.append)
    response = client.post("/api/tasks/task/retry")
    assert response.status_code == 202
    assert response.json()["status"] == "PENDING"
    assert calls[1] == "task"

    monkeypatch.setattr(task_router.document_task_orm, "retry_failed_indexing", lambda *_: False)
    response = client.post("/api/tasks/task/retry")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TASK_NOT_RETRYABLE"


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
        def run(self, document, version, heartbeat, lease):
            heartbeat(1, 2, "child")
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(worker, "DocumentIndexingService", Service)
    monkeypatch.setattr(
        worker.task_orm, "update_index_progress", lambda *args: calls.append("heartbeat")
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
        assert calls == ["heartbeat"] + (["complete"] if outcome else ["fail"])


def test_stale_index_task_fails_without_indexing_or_changing_document(monkeypatch):
    task = SimpleNamespace(
        document_id="doc",
        version=1,
        status=DocumentTaskStatus.RUNNING,
        stage=DocumentTaskStage.INDEXING,
    )
    calls = []
    monkeypatch.setattr(worker.task_orm, "get", lambda task_id: task)
    monkeypatch.setattr(
        worker.document_orm, "get", lambda doc_id: SimpleNamespace(parse_version=2)
    )
    monkeypatch.setattr(
        worker, "DocumentIndexingService", lambda: pytest.fail("过期任务不能索引")
    )
    monkeypatch.setattr(worker.task_orm, "fail", lambda *args: calls.append(args[1]))
    monkeypatch.setattr(
        worker.document_orm,
        "mark_failed",
        lambda *args, **kwargs: calls.append("document"),
    )
    worker.index_document.run("task")
    assert calls == ["文档版本已变化，索引任务已过期"]


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
