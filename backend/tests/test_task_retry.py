# -*- coding: utf-8 -*-
"""统一 retry 的 HTTP 状态码和排队快照响应。"""

import importlib
from datetime import datetime
from datetime import timezone

import pytest

from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage as Stage
from model.document_task.model import DocumentTaskStatus as Status

task_router = importlib.import_module("api.tasks.router")


def _task(stage, status=Status.FAILED):
    return DocumentTask(
        id="task", document_id="doc", version=1, status=status, stage=stage,
        progress=73, step="失败步骤", error_message="failed", lease_token="old",
    )


@pytest.mark.parametrize("stage", [Stage.PENDING, Stage.PARSING, Stage.MERGING, Stage.CHUNKING, Stage.INDEXING])
def test_retry_endpoint_returns_accepted_snapshot_without_rereading(client, monkeypatch, stage):
    failed = _task(stage)
    queued = _task(stage, Status.PENDING)
    queued.step = "等待重试"
    queued.error_message = None
    queued.lease_token = None
    events = []

    def get_task(task_id):
        assert task_id == "task"
        # 重试后的任务可能已经开始，接口必须返回本次接受的排队快照。
        assert events == []
        events.append("get")
        return failed

    def retry(task_id, now):
        assert task_id == "task"
        assert isinstance(now, datetime) and now.tzinfo == timezone.utc
        events.append("retry")
        return queued

    monkeypatch.setattr(task_router.document_task_orm, "get", get_task)
    monkeypatch.setattr(task_router, "retry_failed_task", retry)
    response = client.post("/api/tasks/task/retry")
    assert response.status_code == 202
    assert response.json() == {
        "id": "task", "status": "PENDING", "documentId": "doc", "version": 1,
        "stage": stage.value, "progress": 73, "step": "等待重试", "errorMessage": None,
    }
    assert events == ["get", "retry"]


@pytest.mark.parametrize("status", list(Status))
def test_retry_endpoint_returns_conflict_when_runtime_rejects(client, monkeypatch, status):
    task = _task(Stage.PARSING, status)
    calls = []

    def rejected(task_id, now):
        calls.append(task_id)
        return None

    monkeypatch.setattr(task_router.document_task_orm, "get", lambda _: task)
    monkeypatch.setattr(task_router, "retry_failed_task", rejected)
    response = client.post("/api/tasks/task/retry")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TASK_NOT_RETRYABLE"
    assert calls == ["task"]


def test_retry_endpoint_returns_not_found_without_invoking_runtime(client, monkeypatch):
    def unexpected_retry(*args):
        pytest.fail("不存在的任务不应进入 retry")

    monkeypatch.setattr(task_router.document_task_orm, "get", lambda _: None)
    monkeypatch.setattr(task_router, "retry_failed_task", unexpected_retry)
    response = client.post("/api/tasks/missing/retry")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"
