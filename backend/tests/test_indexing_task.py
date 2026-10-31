# -*- coding: utf-8 -*-
"""索引队列的成功、失败与过期任务。"""

import importlib
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from time import sleep as threading_sleep
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
        lease_token="lease-1",
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
    monkeypatch.setattr(worker.task_orm, "touch_indexing", lambda *args: None)
    def complete(*args):
        calls.append("complete")
        return True

    monkeypatch.setattr(worker.task_orm, "complete", complete)

    def fail(*args):
        calls.append("fail")
        return True

    monkeypatch.setattr(worker.task_orm, "fail", fail)
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
        lease_token="lease-1",
    )
    calls = []
    monkeypatch.setattr(worker.task_orm, "get", lambda task_id: task)
    monkeypatch.setattr(
        worker.document_orm, "get", lambda doc_id: SimpleNamespace(parse_version=2)
    )
    monkeypatch.setattr(
        worker, "DocumentIndexingService", lambda: pytest.fail("过期任务不能索引")
    )

    def fail(*args):
        calls.append(args[1])
        return True

    monkeypatch.setattr(worker.task_orm, "fail", fail)
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
        worker.task_orm, "update_progress", lambda *args: calls.append(args) or True
    )
    monkeypatch.setattr(
        worker.index_document, "apply_async", lambda **kwargs: calls.append(kwargs)
    )
    enqueue_indexing("task", "lease-1")
    assert calls[0][1] == DocumentTaskStage.INDEXING
    assert calls[1] == {"args": ["task"], "queue": "index"}


def test_missed_completion_keeps_the_published_document(monkeypatch, caplog):
    task = SimpleNamespace(
        id="task",
        document_id="doc",
        version=2,
        status=DocumentTaskStatus.RUNNING,
        stage=DocumentTaskStage.INDEXING,
        lease_token="lease-1",
    )
    calls = []
    monkeypatch.setattr(worker.task_orm, "get", lambda task_id: task)
    monkeypatch.setattr(
        worker.document_orm,
        "get",
        lambda doc_id: SimpleNamespace(id="doc", parse_version=2),
    )

    class Service:
        def run(self, document, version, heartbeat, lease):
            return True

    monkeypatch.setattr(worker, "DocumentIndexingService", Service)
    monkeypatch.setattr(worker.task_orm, "touch_indexing", lambda *args: None)
    monkeypatch.setattr(worker.task_orm, "complete", lambda *args: False)
    monkeypatch.setattr(
        worker.document_orm,
        "mark_failed",
        lambda *args, **kwargs: calls.append("document"),
    )
    with caplog.at_level("ERROR"):
        worker.index_document.run("task")
    assert calls == []
    assert "任务完成标记未写上" in caplog.text


def test_replaced_lease_does_not_fail_document(monkeypatch):
    task = SimpleNamespace(
        id="task",
        document_id="doc",
        version=2,
        status=DocumentTaskStatus.RUNNING,
        stage=DocumentTaskStage.INDEXING,
        lease_token="lease-1",
    )
    calls = []
    monkeypatch.setattr(worker.task_orm, "get", lambda task_id: task)
    monkeypatch.setattr(
        worker.document_orm,
        "get",
        lambda doc_id: SimpleNamespace(id="doc", parse_version=2),
    )

    class Service:
        def run(self, document, version, heartbeat, lease):
            raise RuntimeError("Milvus 不可用")

    monkeypatch.setattr(worker, "DocumentIndexingService", Service)
    monkeypatch.setattr(worker.task_orm, "touch_indexing", lambda *args: None)
    monkeypatch.setattr(worker.task_orm, "fail", lambda *args: False)
    monkeypatch.setattr(
        worker.document_orm,
        "mark_failed",
        lambda *args, **kwargs: calls.append("document"),
    )
    worker.index_document.run("task")
    assert calls == []


def test_index_lease_renews_while_the_task_runs(monkeypatch):
    monkeypatch.setattr(worker, "_LEASE_RENEW_SECONDS", 0.01)
    task = SimpleNamespace(
        id="task",
        document_id="doc",
        version=2,
        status=DocumentTaskStatus.RUNNING,
        stage=DocumentTaskStage.INDEXING,
        lease_token="lease-1",
    )
    tokens = []
    monkeypatch.setattr(worker.task_orm, "get", lambda task_id: task)
    monkeypatch.setattr(
        worker.document_orm,
        "get",
        lambda doc_id: SimpleNamespace(id="doc", parse_version=2),
    )

    def touch(task_id, lease_token, now):
        tokens.append(lease_token)

    class Service:
        def run(self, document, version, heartbeat, lease):
            deadline = datetime.now(timezone.utc).timestamp() + 1
            while len(tokens) < 2 and datetime.now(timezone.utc).timestamp() < deadline:
                threading_sleep(0.01)
            return True

    monkeypatch.setattr(worker, "DocumentIndexingService", Service)
    monkeypatch.setattr(worker.task_orm, "touch_indexing", touch)
    monkeypatch.setattr(worker.task_orm, "complete", lambda *args: True)
    worker.index_document.run("task")
    assert tokens[0] == "lease-1"
    assert len(tokens) >= 2


def test_claim_and_index_lease_sql_keep_the_current_token(monkeypatch):
    from model.document_task import orm as task_orm

    statements = []

    def execute_count(sql, params):
        statements.append((" ".join(sql.split()), params))
        return 1

    monkeypatch.setattr(task_orm, "execute_count", execute_count)
    monkeypatch.setattr(task_orm, "get", lambda task_id: None)
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    task_orm.claim("task", now)
    task_orm.touch_indexing("task", "lease-1", now)
    assert task_orm.fail("task", "索引失败", now, "lease-1") is True
    assert "CASE WHEN stage = 'PENDING' THEN 'PARSING' ELSE stage END" in statements[0][0]
    assert "progress =" not in statements[0][0]
    assert "$.lease_token" in statements[0][0]
    assert statements[0][1]["lease_token"]
    assert "$.lease_token" in statements[1][0]
    assert statements[1][1]["lease_token"] == "lease-1"
    assert "$.lease_token" in statements[2][0]
    assert statements[2][1]["lease_token"] == "lease-1"


def test_lost_parse_lease_does_not_dispatch_index_queue(monkeypatch):
    from services.indexing.flow import enqueue_indexing

    monkeypatch.setattr(worker.task_orm, "update_progress", lambda *args: False)
    monkeypatch.setattr(
        worker.index_document, "apply_async", lambda **kwargs: pytest.fail("旧领取者不能投递索引")
    )
    enqueue_indexing("task", "old-lease")


def test_failed_heartbeat_prevents_index_handoff_even_with_same_token(monkeypatch):
    from model.document_task.lease import LeaseLost
    from services.indexing.flow import enqueue_indexing
    from services.task_runtime.lease import TaskLease

    def unexpected(*args, **kwargs):
        pytest.fail("续租失败后不能更新阶段或发送索引任务")

    lease = TaskLease("task", "lease-1")
    lease._lost.set()
    monkeypatch.setattr(worker.task_orm, "update_progress", unexpected)
    monkeypatch.setattr(worker.index_document, "apply_async", unexpected)
    with pytest.raises(LeaseLost):
        enqueue_indexing("task", "lease-1", lease)
