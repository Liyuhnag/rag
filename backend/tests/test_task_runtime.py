# -*- coding: utf-8 -*-
"""公共交接的提交顺序、失效保护与按阶段投递。"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from model.document_task.lease import LeaseLost
from model.document_task.model import DocumentTaskStage as Stage
from services.task_runtime import dispatch as dispatch_module
from services.task_runtime import lifecycle
from services.task_runtime.lease import TaskLease
from services.task_runtime.policy import POLICIES


@pytest.mark.parametrize("stage", list(POLICIES))
def test_dispatch_uses_stage_queue_and_limits(monkeypatch, stage):
    from task.index_document import index_document
    from task.parse_document import parse_document

    calls = []
    monkeypatch.setattr(index_document, "apply_async", lambda **kw: calls.append(("index", kw)))
    monkeypatch.setattr(parse_document, "apply_async", lambda **kw: calls.append(("parse", kw)))
    assert dispatch_module.dispatch("task", stage)
    queue, message = calls[0]
    assert queue == ("index" if stage == Stage.INDEXING else "parse")
    assert message["queue"] == queue
    assert message["args"] == ["task"]
    assert 0 < message["soft_time_limit"] < message["time_limit"]


def test_handoff_commits_before_broker_failure_and_cannot_be_reused(monkeypatch):
    events = []
    state = SimpleNamespace(status="RUNNING", token="old", stage=Stage.CHUNKING)

    @contextmanager
    def guard(*args):
        if state.token != "old":
            raise LeaseLost("task")
        events.append("begin")
        yield
        events.append("commit")

    def handoff(*args):
        state.status = "PENDING"
        state.stage = Stage.INDEXING
        state.token = None
        return True

    def dispatch(*args):
        assert events[-1] == "commit"
        events.append("broker-failed")
        return False

    monkeypatch.setattr(lifecycle.task_orm, "lease_guard", guard)
    monkeypatch.setattr(lifecycle.task_orm, "advance", handoff)
    monkeypatch.setattr(lifecycle, "dispatch", dispatch)
    lease = TaskLease("task", "old")
    assert lifecycle.advance(lease, Stage.INDEXING, 90, "等待索引")
    assert (state.status, state.stage, state.token) == ("PENDING", Stage.INDEXING, None)
    with pytest.raises(LeaseLost):
        lifecycle.advance(lease, Stage.INDEXING, 90, "旧消息")
    assert events == ["begin", "commit", "broker-failed"]


def test_failed_heartbeat_prevents_handoff(monkeypatch):
    lease = TaskLease("task", "old")
    lease._lost.set()
    monkeypatch.setattr(lifecycle, "dispatch", lambda *args: pytest.fail("失效后不能投递"))
    with pytest.raises(LeaseLost):
        lifecycle.advance(lease, Stage.INDEXING, 90, "等待索引")


def test_dispatch_broker_error_returns_false_without_state_mutation(monkeypatch):
    from task.index_document import index_document

    def unavailable(**kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(index_document, "apply_async", unavailable)
    assert not dispatch_module.dispatch("task", Stage.INDEXING)
