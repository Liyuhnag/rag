# -*- coding: utf-8 -*-
"""索引队列的成功、失败与过期任务。"""

import importlib
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace

import pytest

from model.document_task.model import DocumentTaskStage

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
    monkeypatch.setattr(task_router, "dispatch_task", lambda task_id, stage: calls.append(task_id))
    response = client.post("/api/tasks/task/retry")
    assert response.status_code == 202
    assert response.json()["status"] == "PENDING"
    assert calls[1] == "task"

    monkeypatch.setattr(task_router.document_task_orm, "retry_failed_indexing", lambda *_: False)
    response = client.post("/api/tasks/task/retry")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TASK_NOT_RETRYABLE"


def _prepare(monkeypatch):
    from model.document.model import DocumentStatus
    from services.task_runtime import lifecycle

    task = SimpleNamespace(
        id="task", document_id="doc", version=2, status="RUNNING",
        stage=DocumentTaskStage.INDEXING, lease_token="lease-1",
    )
    document = SimpleNamespace(id="doc", parse_version=2, status=DocumentStatus.PROCESSING)
    calls = []

    def claim(task_id, now, stages):
        assert stages == (DocumentTaskStage.INDEXING,)
        return task

    def complete(*args):
        task.status = "COMPLETED"
        task.stage = DocumentTaskStage.DONE
        calls.append("complete")
        return True

    def fail(*args):
        task.status = "FAILED"
        calls.append("fail")
        return True

    def supersede(*args):
        task.status = "SUPERSEDED"
        calls.append("supersede")
        return True

    def mark_failed(*args, **kwargs):
        document.status = "FAILED"

    monkeypatch.setattr(worker.task_orm, "claim", claim)
    monkeypatch.setattr(worker.document_orm, "get", lambda *args: document)
    monkeypatch.setattr(worker.task_orm, "complete", complete)
    monkeypatch.setattr(worker.task_orm, "fail", fail)
    monkeypatch.setattr(worker.task_orm, "supersede", supersede)
    monkeypatch.setattr(worker.task_orm, "update_index_progress", lambda *args: calls.append("progress"))
    monkeypatch.setattr(lifecycle.document_orm, "mark_failed", mark_failed)
    return task, document, calls


@pytest.mark.usefixtures("parse_lease")
@pytest.mark.parametrize("outcome", [True, False, RuntimeError("Milvus unavailable")])
def test_indexing_completes_only_after_success(monkeypatch, outcome):
    task, document, calls = _prepare(monkeypatch)

    class Service:
        def run(self, document, version, heartbeat, lease):
            lease()
            heartbeat(1, 1, "child")
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(worker, "DocumentIndexingService", Service)
    if isinstance(outcome, Exception):
        with pytest.raises(RuntimeError, match="Milvus unavailable"):
            worker.index_document.run("task")
        assert task.status == document.status == "FAILED"
    else:
        worker.index_document.run("task")
        assert task.status == ("COMPLETED" if outcome else "SUPERSEDED")
        assert document.status == "PROCESSING"
    assert calls[0] == "progress"


@pytest.mark.usefixtures("parse_lease")
def test_stale_indexing_does_not_touch_new_document(monkeypatch):
    task, document, calls = _prepare(monkeypatch)
    document.parse_version = 3
    document.status = "READY"
    monkeypatch.setattr(worker, "DocumentIndexingService", lambda: pytest.fail("旧版本不能索引"))
    worker.index_document.run(task.id)
    assert task.status == "SUPERSEDED"
    assert document.status == "READY"
    assert calls == ["supersede"]


@pytest.mark.usefixtures("parse_lease")
def test_published_legacy_task_only_repairs_completion(monkeypatch):
    task, document, calls = _prepare(monkeypatch)
    document.status = "READY"
    monkeypatch.setattr(worker, "DocumentIndexingService", lambda: pytest.fail("已发布不应重建"))
    worker.index_document.run(task.id)
    assert task.status == "COMPLETED"
    assert document.status == "READY"
    assert calls == ["complete"]


def test_unclaimed_index_message_does_no_work(monkeypatch):
    monkeypatch.setattr(worker.task_orm, "claim", lambda *args: None)
    monkeypatch.setattr(worker.document_orm, "get", lambda *args: pytest.fail("不能执行未领取消息"))
    worker.index_document.run("task")


@pytest.mark.usefixtures("parse_lease")
def test_failed_initial_renewal_prevents_external_work(monkeypatch):
    task, document, calls = _prepare(monkeypatch)
    monkeypatch.setattr(worker.task_orm, "renew", lambda *args: False)
    monkeypatch.setattr(worker, "DocumentIndexingService", lambda: pytest.fail("失效后不能索引"))
    worker.index_document.run(task.id)
    assert task.status == "RUNNING"
    assert document.status == "PROCESSING"
    assert calls == []
