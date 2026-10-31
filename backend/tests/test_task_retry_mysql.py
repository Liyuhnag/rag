# -*- coding: utf-8 -*-
"""统一 retry 的真实 MySQL 准入、并发、回滚与分段接续测试。"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from model.common import database
from model.document import orm as document_orm
from model.document_task import orm
from model.document_task.model import DocumentTaskStage as Stage
from model.parse_job import orm as job_orm
from model.parse_job.model import ParseJobStatus as PartStatus
from services.parsing import poll
from services.task_runtime import lifecycle
from services.task_runtime.lease import TaskLease
from tests.test_poll_parse import _job
# 复用随机前缀隔离表；本模块同样需要 RAG_TEST_MYSQL=1 才会运行。
from tests.test_task_lease_mysql import mysql as mysql
from tests.test_task_lease_mysql import mysql_config as mysql_config
from tests.test_task_lease_mysql import pytestmark as pytestmark


def _prepare_failed(now, stage=Stage.PARSING):
    result = {
        "lease_token": "old", "embedded_count": 3, "es_indexed": 2, "milvus_indexed": 3,
        "last_chunk_id": "child-3", "checkpoint": {"indexing": {"index_version": 7}},
    }
    database.execute(
        "UPDATE document_tasks SET status='FAILED', stage=%(stage)s, progress=73, "
        "step='失败步骤', error_message='task error', result_json=%(result)s WHERE id='task'",
        {"stage": stage.value, "result": json.dumps(result)},
    )
    database.execute(
        "UPDATE documents SET status='FAILED', error_message='document error', "
        "parsed_md_key='parsed.md', parsed_json_key='parsed.json', chunk_count=3 WHERE id='doc'",
    )
    return result


def _snapshot():
    return {
        table: database.execute(f"SELECT * FROM {table} ORDER BY id")
        for table in ("documents", "document_tasks", "parse_jobs")
    }


def _insert_part(now, part_id, **kwargs):
    values = {
        "id": part_id, "task_id": "task", "document_id": "doc", "version": 1,
        "lease_token": "group", "status": PartStatus.FAILED, "attempts": 3,
        "error_message": "part error", "next_poll_at": None,
        "created_at": now, "updated_at": now,
    }
    values.update(kwargs)
    job_orm.insert(_job(**values))


@pytest.mark.parametrize("stage", [Stage.PENDING, Stage.PARSING, Stage.MERGING, Stage.CHUNKING, Stage.INDEXING])
def test_retry_accepts_each_executable_stage_and_preserves_artifacts(mysql, monkeypatch, stage):
    result = _prepare_failed(mysql, stage)
    before = _snapshot()
    messages = []
    monkeypatch.setattr(lifecycle, "dispatch", lambda *args: messages.append(args))
    now = mysql + timedelta(seconds=1)
    task = lifecycle.retry("task", now)
    assert (task.status, task.stage, task.progress, task.step) == ("PENDING", stage, 73, "等待重试")
    assert task.lease_token is None and task.error_message is None
    after = _snapshot()
    expected_document = dict(before["documents"][0], status="PROCESSING", error_message=None,
                             updated_at=database.as_utc(now))
    assert after["documents"] == [expected_document]
    result.pop("lease_token")
    row = after["document_tasks"][0]
    assert json.loads(row["result_json"]) == result
    expected_task = dict(before["document_tasks"][0], status="PENDING", error_message=None,
                         step="等待重试", updated_at=database.as_utc(now), result_json=row["result_json"])
    assert after["document_tasks"] == [expected_task]
    assert after["parse_jobs"] == []
    assert messages == [("task", stage)]


@pytest.mark.parametrize("status, stage, document_status, version_delta", [
    ("PENDING", "PARSING", "FAILED", 0),
    ("RUNNING", "PARSING", "FAILED", 0),
    ("SUPERSEDED", "PARSING", "FAILED", 0),
    ("COMPLETED", "DONE", "FAILED", 0),
    ("FAILED", "PARSING", "UPLOADED", 0),
    ("FAILED", "PARSING", "PROCESSING", 0),
    ("FAILED", "PARSING", "READY", 0),
    ("FAILED", "PARSING", "FAILED", -1),
    ("FAILED", "PARSING", "FAILED", 1),
    ("FAILED", "DONE", "FAILED", 0),
    ("FAILED", "PARSING", None, 0),
])
def test_retry_rejects_ineligible_tasks_without_any_writes(
    mysql, monkeypatch, status, stage, document_status, version_delta,
):
    _prepare_failed(mysql)
    _insert_part(mysql, "failed-part")
    database.execute(
        "UPDATE document_tasks SET status=%(status)s, stage=%(stage)s WHERE id='task'",
        {"status": status, "stage": stage},
    )
    if document_status is None:
        database.execute("DELETE FROM documents WHERE id='doc'")
    else:
        database.execute(
            "UPDATE documents SET status=%(status)s, parse_version=%(version)s WHERE id='doc'",
            {"status": document_status, "version": 1 + version_delta},
        )
    before = _snapshot()
    messages = []
    monkeypatch.setattr(lifecycle, "dispatch", lambda *args: messages.append(args))
    assert lifecycle.retry("task", mysql) is None
    assert _snapshot() == before
    assert messages == []


def test_retry_missing_task_changes_nothing(mysql, monkeypatch):
    before = _snapshot()
    messages = []
    monkeypatch.setattr(lifecycle, "dispatch", lambda *args: messages.append(args))
    assert lifecycle.retry("missing", mysql) is None
    assert _snapshot() == before
    assert messages == []


def test_concurrent_retries_prepare_and_dispatch_only_once(mysql, monkeypatch):
    _prepare_failed(mysql)
    start = Barrier(2)
    prepared = []
    messages = []
    prepare_parts = job_orm.retry_failed_parts

    def prepare(*args):
        prepared.append(args)
        prepare_parts(*args)

    def retry():
        start.wait(timeout=3)
        return lifecycle.retry("task", mysql)

    monkeypatch.setattr(job_orm, "retry_failed_parts", prepare)
    monkeypatch.setattr(lifecycle, "dispatch", lambda *args: messages.append(args))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(retry) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert sum(task is not None for task in results) == 1
    assert len(prepared) == 1
    assert messages == [("task", Stage.PARSING)]
    assert (orm.get("task").status, document_orm.get("doc").status) == ("PENDING", "PROCESSING")


@pytest.mark.parametrize("failure_sql", ["UPDATE parse_jobs", "UPDATE documents", "UPDATE document_tasks"])
def test_retry_rolls_back_parts_document_and_task_on_any_write_failure(mysql, monkeypatch, failure_sql):
    _prepare_failed(mysql)
    _insert_part(mysql, "failed-part")
    before = _snapshot()
    connect = database.pymysql.connect
    messages = []

    def failing_connection(**kwargs):
        connection = connect(**kwargs)

        class FailingCursor(connection.cursorclass):
            def execute(self, query, args=None):
                result = super().execute(query, args)
                # 先执行真实写入再抛错，证明已经写入的变更也能回滚。
                if query.strip().startswith(failure_sql):
                    raise RuntimeError("retry write failed")
                return result

        connection.cursorclass = FailingCursor
        return connection

    monkeypatch.setattr(database.pymysql, "connect", failing_connection)
    monkeypatch.setattr(lifecycle, "dispatch", lambda *args: messages.append(args))
    with pytest.raises(RuntimeError, match="retry write failed"):
        lifecycle.retry("task", mysql)
    assert _snapshot() == before
    assert messages == []


def test_retry_commits_before_dispatch_and_broker_failure_is_supplemented(mysql, monkeypatch):
    _prepare_failed(mysql, Stage.INDEXING)
    messages = []

    def broker_unavailable(task_id, stage):
        def read_committed():
            return orm.get(task_id), document_orm.get("doc")

        with ThreadPoolExecutor(max_workers=1) as pool:
            task, document = pool.submit(read_committed).result(timeout=3)
        assert (task.status, task.lease_token, document.status) == ("PENDING", None, "PROCESSING")
        messages.append((task_id, stage))
        return False

    monkeypatch.setattr(lifecycle, "dispatch", broker_unavailable)
    assert lifecycle.retry("task", mysql) is not None
    assert lifecycle.retry("task", mysql) is None
    assert messages == [("task", Stage.INDEXING)]
    assert [task.id for task in orm.find_pending(mysql + timedelta(minutes=5), 10)] == ["task"]


@pytest.mark.parametrize("stage", [Stage.PARSING, Stage.MERGING])
def test_retry_reuses_zip_and_active_parts_and_resubmits_only_failed_part(mysql, monkeypatch, stage):
    _prepare_failed(mysql, stage)
    for index, (part_id, status, zip_key) in enumerate([
        ("failed", PartStatus.FAILED, None),
        ("failed-zip", PartStatus.FAILED, "saved.zip"),
        ("completed", PartStatus.COMPLETED, "complete.zip"),
        ("submitted", PartStatus.SUBMITTED, None),
        ("running", PartStatus.RUNNING, None),
        ("downloading", PartStatus.DOWNLOADING, None),
        ("downloaded", PartStatus.DOWNLOADING, "downloaded.zip"),
    ], start=1):
        _insert_part(
            mysql, part_id, part_index=index, status=status, result_key=zip_key,
            next_poll_at=None if status == PartStatus.FAILED else mysql + timedelta(hours=1),
        )
    for part_id, overrides in [
        ("old-group", {"lease_token": "older", "created_at": mysql - timedelta(hours=1)}),
        ("wrong-task", {"task_id": "other-task", "lease_token": "other-task"}),
        ("wrong-document", {"document_id": "other-doc"}),
        ("wrong-version", {"version": 2}),
        ("legacy", {"lease_token": "legacy"}),
    ]:
        _insert_part(mysql, part_id, **overrides)
    database.execute("UPDATE parse_jobs SET lease_token=NULL WHERE id='legacy'")
    before = {row["id"]: row for row in _snapshot()["parse_jobs"]}
    messages = []
    monkeypatch.setattr(lifecycle, "dispatch", lambda *args: messages.append(args))
    assert lifecycle.retry("task", mysql) is not None
    after = {row["id"]: row for row in _snapshot()["parse_jobs"]}
    for part_id in before:
        if part_id not in {"failed", "failed-zip"}:
            assert after[part_id] == before[part_id]
    failed = job_orm.get("failed")
    assert (failed.status, failed.attempts, failed.mineru_job_id, failed.mineru_file_id) == (
        "SUBMITTED", 0, None, None,
    )
    assert failed.next_poll_at == mysql and failed.error_message is None
    saved = job_orm.get("failed-zip")
    assert (saved.status, saved.result_key, saved.mineru_job_id, saved.attempts) == (
        "COMPLETED", "saved.zip", "mineru-1", 3,
    )
    assert saved.next_poll_at is None and saved.error_message is None
    # 尚未领取时不能轮询，旧 token 的消息也不能提前执行。
    assert job_orm.claim_due(mysql, 10) == []
    task = orm.claim("task", mysql, (stage,))
    if stage == Stage.MERGING:
        poll.run_merge(task, document_orm.get("doc"), TaskLease("task", task.lease_token, (stage,)))
        assert orm.get("task").stage == "PARSING"
        task = orm.claim("task", mysql, (Stage.PARSING,))
    lease = TaskLease("task", task.lease_token, (Stage.PARSING,))
    assert poll.resume_parts(task, lease)
    assert job_orm.get("failed").lease_token == task.lease_token
    due = job_orm.claim_due(mysql, 10)
    assert [job.id for job in due] == ["failed"]
    resubmissions = []
    monkeypatch.setattr(poll, "_enqueue_resubmit", lambda *args: resubmissions.append(args))
    poll.poll_job(due[0], object(), mysql)
    assert resubmissions == [("failed", task.lease_token)]
