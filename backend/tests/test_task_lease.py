# -*- coding: utf-8 -*-
"""租约失效必须锁存，后台续租必须与业务事务隔离。"""

from contextlib import nullcontext
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace
from threading import Event

import pytest

from model.document_task import orm
from model.document_task.lease import LeaseLost
from model.document_task.model import DocumentTaskStage
from services.task_runtime.lease import TaskLease


def test_background_heartbeat_renews_and_stops(monkeypatch):
    renewed = Event()
    calls = []

    def renew(task_id, token, now, stages):
        calls.append((task_id, token))
        if len(calls) >= 2:
            renewed.set()
        return True

    monkeypatch.setattr(orm, "renew", renew)
    with TaskLease("task", "original", interval=0.01) as lease:
        assert renewed.wait(2)
    assert calls[0] == calls[1] == ("task", "original")
    assert not lease._thread.is_alive()


@pytest.mark.parametrize("db_error", [False, True])
def test_failed_heartbeat_is_latched_even_if_database_recovers(monkeypatch, db_error):
    calls = []

    def renew(*args):
        calls.append(args)
        if len(calls) == 1:
            return True
        if db_error:
            raise RuntimeError("database disconnected")
        return False

    monkeypatch.setattr(orm, "renew", renew)
    monkeypatch.setattr(orm, "owns_lease", lambda *args: True)
    monkeypatch.setattr(orm, "lease_guard", lambda *args: pytest.fail("失效后不能写入"))
    with TaskLease("task", "old", interval=0.01) as lease:
        assert lease._lost.wait(2)
        with pytest.raises(LeaseLost):
            lease.ensure()
        with pytest.raises(LeaseLost):
            with lease.writes():
                pytest.fail("失效后不能写入")


def test_transaction_lock_replaces_heartbeat_without_self_deadlock(monkeypatch):
    calls = []
    monkeypatch.setattr(orm, "renew", lambda *args: calls.append(args) or True)
    monkeypatch.setattr(orm, "lease_guard", lambda *args: nullcontext())
    with TaskLease("task", "old", interval=0.01) as lease:
        with lease.writes():
            Event().wait(0.05)
            assert len(calls) == 1
        assert not lease._lost.is_set()


def test_claim_does_not_adopt_a_new_claimants_token(monkeypatch):
    monkeypatch.setattr(orm, "execute_count", lambda *args: 1)
    monkeypatch.setattr(
        orm, "get", lambda *args: SimpleNamespace(lease_token="new-claim")
    )
    assert orm.claim("task", datetime.now(timezone.utc)) is None


@pytest.mark.parametrize("operation", [orm.complete, orm.fail])
def test_terminal_operations_require_nonempty_token(operation):
    now = datetime.now(timezone.utc)
    with pytest.raises(TypeError):
        operation("task", "done", now)
    with pytest.raises(ValueError):
        operation("task", "done", now, "")


def test_renew_same_timestamp_checks_ownership_and_stage(monkeypatch):
    monkeypatch.setattr(orm, "execute_count", lambda *args: 0)
    monkeypatch.setattr(orm, "execute", lambda *args: [{"stage": "PARSING"}])
    now = datetime.now(timezone.utc)
    assert orm.renew("task", "token", now, (DocumentTaskStage.PARSING,))
    assert not orm.renew("task", "token", now, (DocumentTaskStage.CHUNKING,))
