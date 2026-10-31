# -*- coding: utf-8 -*-
"""MinerU 分段轮询、重新提交和下载合并。"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest

pytestmark = pytest.mark.usefixtures("parse_lease")

from model.document.model import Document
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from model.document_task.model import DocumentTaskStatus
from model.document.model import DocumentStatus
from model.parse_job.model import ParseJob
from model.parse_job.model import ParseJobStatus
from services.parsing.errors import MinerUJobLost
from services.parsing.errors import MinerURequestError
from services.parsing.errors import ParserUnavailable
from services.parsing.mineru.client import MinerUJob
from services.parsing.mineru.client import MinerUStatus
from services.parsing.poll import DOWNLOAD_LEASE
from services.parsing.poll import POLL_INTERVAL
from services.parsing.poll import apply_plan
from services.parsing.poll import download_job
from services.parsing.poll import merge_ready
from services.parsing.poll import plan_lost
from services.parsing.poll import plan_status
from services.parsing.poll import plan_unavailable
from services.parsing.poll import poll_job
from services.parsing.poll import resubmit_job
from services.parsing.service import StoredParseResult

poll = __import__("services.parsing.poll", fromlist=["poll"])


def _now() -> datetime:
    return datetime(2026, 10, 23, tzinfo=timezone.utc)


def _job(**kwargs: object) -> ParseJob:
    now = _now()
    values: dict[str, object] = {
        "id": "job-1",
        "document_id": "doc-1",
        "task_id": "task-1",
        "lease_token": "merge-lease",
        "version": 2,
        "part_index": 1,
        "page_start": 1,
        "page_end": 10,
        "source_key": "documents/doc-1/parse/2/parts/1/source.pdf",
        "mineru_file_id": "file-1",
        "mineru_job_id": "mineru-1",
        "status": ParseJobStatus.SUBMITTED,
        "attempts": 1,
        "next_poll_at": now,
        "error_message": None,
        "result_key": None,
        "created_at": now,
        "updated_at": now,
    }
    values.update(kwargs)
    return ParseJob(**values)  # type: ignore[arg-type]


def _document() -> Document:
    now = _now()
    return Document(
        id="doc-1",
        name="demo.pdf",
        file_type="pdf",
        size_bytes=20,
        chunk_count=0,
        status=DocumentStatus.PROCESSING,
        uploaded_at=now,
        updated_at=now,
        object_key="documents/doc-1/demo.pdf",
        file_url="",
        previewable=False,
        preview_url=None,
        parse_version=2,
    )


def _task() -> DocumentTask:
    return DocumentTask(
        id="task-1",
        document_id="doc-1",
        version=2,
        status=DocumentTaskStatus.RUNNING,
        stage=DocumentTaskStage.PARSING,
        progress=5,
        step="等待解析结果",
        lease_token="merge-lease",
    )


class _Mineru:
    def __init__(self, state="completed", error=None, exc=None, body=b"zip"):
        self.state = state
        self.error = error
        self.exc = exc
        self.body = body
        self.submitted = MinerUJob(file_id="file-2", job_id="mineru-2")

    def status(self, job_id):
        if self.exc is not None:
            raise self.exc
        return MinerUStatus(state=self.state, error=self.error)

    def download(self, job_id, target_dir):
        path = target_dir / "result.zip"
        path.write_bytes(self.body)
        return path

    def submit(self, path, filename, sha256):
        return self.submitted


class _Reader:
    def __init__(self, objects=None):
        self.objects = {} if objects is None else dict(objects)
        self.written = []

    def read_object(self, key):
        return self.objects[key]

    def write_object(self, key, content, content_type):
        self.objects[key] = content
        self.written.append((key, content_type))

    def upload_file(self, key, path, content_type):
        self.write_object(key, path.read_bytes(), content_type)

    def download_object(self, key, path):
        path.write_bytes(self.objects[key])


def test_queued_and_running_wait_ten_seconds():
    now = _now()
    job = _job()
    queued = plan_status(job, "queued", None, now)
    running = plan_status(job, "running", None, now)
    assert queued.status == ParseJobStatus.SUBMITTED
    assert running.status == ParseJobStatus.RUNNING
    assert queued.next_poll_at == now + POLL_INTERVAL
    assert running.download is False
    assert queued.resubmit is False


def test_completed_downloads_without_merging_inside_poll():
    now = _now()
    plan = plan_status(_job(), "completed", None, now)
    assert plan.status == ParseJobStatus.DOWNLOADING
    assert plan.download is True
    assert plan.next_poll_at == now + DOWNLOAD_LEASE


def test_failed_resubmits_until_attempt_limit():
    now = _now()
    retry = plan_status(_job(attempts=2), "failed", "超时", now)
    assert retry.resubmit is True
    assert retry.fail_document is False
    assert retry.error_message == "超时"
    lost = plan_lost(_job(attempts=1), now)
    assert lost.resubmit is True
    assert lost.error_message == "MinerU 任务不存在"
    stopped = plan_status(_job(attempts=3), "failed", "超时", now)
    assert stopped.fail_document is True
    assert stopped.status == ParseJobStatus.FAILED


def test_unavailable_backs_off_without_failing():
    now = _now()
    plan = plan_unavailable(_job(attempts=1), "MinerU 暂不可用", now)
    assert plan.fail_document is False
    assert plan.resubmit is False
    assert plan.status == ParseJobStatus.SUBMITTED
    assert plan.next_poll_at == now + timedelta(seconds=10)


def test_poll_completed_enqueues_download(monkeypatch):
    now = _now()
    seen = {}
    downloads = []
    monkeypatch.setattr(
        poll.parse_job_orm,
        "set_status",
        lambda job_id, status, moment, next_poll_at, error: seen.update(
            {
                "status": status,
                "next_poll_at": next_poll_at,
                "error": error,
            }
        ),
    )
    monkeypatch.setattr(poll, "_enqueue_download", downloads.append)
    poll_job(_job(), _Mineru(state="completed"), now)
    assert seen["status"] == ParseJobStatus.DOWNLOADING
    assert downloads == ["job-1"]


def test_poll_lost_job_enqueues_resubmit(monkeypatch):
    now = _now()
    retries = []
    downloads = []
    monkeypatch.setattr(
        poll.parse_job_orm,
        "mark_retry",
        lambda job_id, next_poll_at, error, moment: retries.append(error),
    )
    monkeypatch.setattr(
        poll, "_enqueue_resubmit", lambda job_id: downloads.append(job_id)
    )
    poll_job(_job(attempts=1), _Mineru(exc=MinerUJobLost("丢失")), now)
    assert retries == ["MinerU 任务不存在"]
    assert downloads == ["job-1"]


def test_poll_rejected_fails_document(monkeypatch):
    now = _now()
    failed = []
    monkeypatch.setattr(poll.parse_job_orm, "set_status", lambda *args: None)
    monkeypatch.setattr(
        poll.task_orm,
        "fail",
        lambda task_id, message, moment, token: failed.append(message),
    )
    monkeypatch.setattr(poll.document_orm, "mark_failed", lambda *args, **kwargs: None)
    poll_job(_job(), _Mineru(exc=MinerURequestError("拒绝")), now)
    assert failed == ["拒绝"]


def test_downloading_job_is_enqueued_again(monkeypatch):
    downloads = []
    monkeypatch.setattr(poll, "_enqueue_download", downloads.append)
    poll_job(
        _job(status=ParseJobStatus.DOWNLOADING),
        _Mineru(exc=AssertionError("不应查询")),
        _now(),
    )
    assert downloads == ["job-1"]


def test_resubmit_records_new_mineru_ids(monkeypatch):
    now = _now()
    job = _job()
    recorded = {}
    reader = _Reader({job.source_key: b"pdf-bytes"})
    monkeypatch.setattr(poll.parse_job_orm, "get", lambda job_id: job)
    monkeypatch.setattr(
        poll.parse_job_orm,
        "record_submission",
        lambda *args: recorded.update({"args": args}) or True,
    )
    resubmit_job(job.id, _Mineru(), reader, now)
    assert recorded["args"][1] == "mineru-1"
    assert recorded["args"][2] == "file-2"
    assert recorded["args"][3] == "mineru-2"
    assert recorded["args"][4] == now + POLL_INTERVAL


def test_resubmit_transport_error_does_not_fail(monkeypatch):
    now = _now()
    job = _job()
    statuses = []
    monkeypatch.setattr(poll.parse_job_orm, "get", lambda job_id: job)
    monkeypatch.setattr(
        poll.parse_job_orm,
        "set_status",
        lambda job_id, status, moment, next_poll_at, error: statuses.append(
            (status, error)
        ),
    )
    reader = _Reader({job.source_key: b"pdf"})
    mineru = _Mineru()

    def _submit(path, filename, sha256):
        raise ParserUnavailable("MinerU 暂不可用")

    mineru.submit = _submit
    resubmit_job(job.id, mineru, reader, now)
    assert statuses == [(ParseJobStatus.SUBMITTED, "MinerU 暂不可用")]


def test_download_waits_when_a_sibling_is_missing(monkeypatch):
    now = _now()
    job = _job(status=ParseJobStatus.DOWNLOADING)
    sibling = _job(
        id="job-2",
        part_index=2,
        status=ParseJobStatus.RUNNING,
        result_key=None,
    )
    reader = _Reader()
    saved = []
    merged = []
    monkeypatch.setattr(poll.parse_job_orm, "get", lambda job_id: job)
    monkeypatch.setattr(
        poll.parse_job_orm,
        "save_zip",
        lambda job_id, key, moment: saved.append(key) or True,
    )
    monkeypatch.setattr(
        poll.parse_job_orm,
        "list_by_document_version",
        lambda document_id, version, token: [job, sibling],
    )
    monkeypatch.setattr(
        poll.task_orm,
        "begin_merge",
        lambda task_id, moment, token: merged.append(task_id) or True,
    )
    download_job(job.id, _Mineru(body=b"PK"), reader, now)
    assert len(saved) == 1
    assert saved[0].startswith("documents/doc-1/parse/2/attempts/merge-lease/parts/1/")
    assert saved[0].endswith(".zip")
    assert reader.written[0][1] == "application/zip"
    assert merged == []


@pytest.mark.parametrize(
    ("error", "chunk_result"),
    [(None, True), (None, False), (RuntimeError("切分失败"), None)],
)
def test_download_merges_when_every_part_has_a_zip(monkeypatch, error, chunk_result):
    now = _now()
    first = _job(
        status=ParseJobStatus.DOWNLOADING,
        result_key="documents/doc-1/parse/2/parts/1/result.zip",
    )
    second = _job(
        id="job-2",
        part_index=2,
        page_start=11,
        page_end=20,
        status=ParseJobStatus.DOWNLOADING,
        result_key="documents/doc-1/parse/2/parts/2/result.zip",
    )
    reader = _Reader(
        {
            first.result_key: b"one",
            second.result_key: b"two",
        }
    )
    recorded = []
    completed = []
    collected = []

    class _Service:
        def __init__(self, reader, attempt_id=None):
            self.reader = reader

        def collect(self, document_id, version, file_type, source_key, parts):
            collected.append((source_key, [part.part_index for part in parts]))
            return StoredParseResult(
                md_key="documents/doc-1/parsed/2/merged.md",
                json_key="documents/doc-1/parsed/2/merged.json",
                manifest_key="documents/doc-1/parsed/2/manifest.json",
                part_results=(
                    (1, "documents/doc-1/parse/2/parts/1/manifest.json"),
                    (2, "documents/doc-1/parse/2/parts/2/manifest.json"),
                ),
            )

    monkeypatch.setattr(poll.parse_job_orm, "get", lambda job_id: first)
    monkeypatch.setattr(
        poll.parse_job_orm,
        "list_by_document_version",
        lambda document_id, version, token: [first, second],
    )
    monkeypatch.setattr(poll.task_orm, "begin_merge", lambda task_id, moment, token: True)
    monkeypatch.setattr(poll.task_orm, "get", lambda task_id: _task())
    monkeypatch.setattr(poll.document_orm, "get", lambda document_id: _document())
    monkeypatch.setattr(
        poll.document_orm,
        "set_parsed_artifacts",
        lambda document_id, md_key, json_key, moment, **kwargs: recorded.append(md_key),
    )
    monkeypatch.setattr(
        poll.parse_job_orm,
        "record_result",
        lambda job_id, key, moment: recorded.append((job_id, key)),
    )
    monkeypatch.setattr(
        poll.task_orm,
        "complete",
        lambda task_id, step, moment, token: completed.append(step),
    )

    class _Chunking:
        def run(self, document, version, md_key, json_key):
            recorded.append(("chunk", document.id, version, md_key, json_key))
            if error is not None:
                raise error
            return chunk_result

    monkeypatch.setattr(
        poll.task_orm,
        "fail",
        lambda task_id, message, moment, token=None: completed.append(("fail", message)) or True,
    )
    monkeypatch.setattr(
        poll.document_orm,
        "mark_failed",
        lambda document_id, message, moment, **kwargs: completed.append(
            ("document_failed", kwargs["expected_version"])
        ),
    )
    monkeypatch.setattr(poll, "DocumentChunkingService", _Chunking, raising=False)
    monkeypatch.setattr(
        poll,
        "enqueue_indexing",
        lambda *args: completed.append("索引完成"),
        raising=False,
    )
    monkeypatch.setattr(
        poll.task_orm,
        "update_progress",
        lambda task_id, stage, percent, step, moment, token: recorded.append((stage, percent)),
    )
    monkeypatch.setattr(poll, "DocumentParsingService", _Service)
    if error is None:
        download_job(first.id, _Mineru(), reader, now)
    else:
        with pytest.raises(RuntimeError, match="切分失败"):
            download_job(first.id, _Mineru(), reader, now)
    assert collected == [("documents/doc-1/demo.pdf", [1, 2])]
    assert recorded == [
        "documents/doc-1/parsed/2/merged.md",
        ("job-1", "documents/doc-1/parse/2/parts/1/manifest.json"),
        ("job-2", "documents/doc-1/parse/2/parts/2/manifest.json"),
        (DocumentTaskStage.CHUNKING, 85),
        (
            "chunk",
            "doc-1",
            2,
            "documents/doc-1/parsed/2/merged.md",
            "documents/doc-1/parsed/2/merged.json",
        ),
    ]
    if error is None and chunk_result:
        assert completed == ["索引完成"]
    elif error is None:
        assert completed == [("fail", "文档版本不匹配，解析任务已过期")]
    else:
        assert completed == [("fail", "切分失败"), ("document_failed", 2)]


def test_second_merge_claim_does_not_collect(monkeypatch):
    job = _job(
        status=ParseJobStatus.DOWNLOADING,
        result_key="documents/doc-1/parse/2/parts/1/result.zip",
    )
    called = []
    monkeypatch.setattr(
        poll.parse_job_orm,
        "list_by_document_version",
        lambda document_id, version, token: [job],
    )
    monkeypatch.setattr(poll.task_orm, "begin_merge", lambda task_id, moment, token: False)
    monkeypatch.setattr(poll.task_orm, "get", lambda task_id: _task())
    monkeypatch.setattr(
        poll,
        "DocumentParsingService",
        lambda reader: called.append(reader),
    )
    merge_ready(job, _Reader({job.result_key: b"zip"}), _now())
    assert called == []


@pytest.mark.parametrize("version", [1, 3])
def test_stale_download_fails_task_without_changing_document(monkeypatch, version):
    job = _job(
        status=ParseJobStatus.DOWNLOADING,
        result_key="parts/result.zip",
    )
    document = _document()
    document.parse_version = version
    document.status = DocumentStatus.READY
    document.parsed_md_key = "current.md"
    task = _task()
    failures = []
    monkeypatch.setattr(poll.parse_job_orm, "get", lambda job_id: job)
    monkeypatch.setattr(
        poll.parse_job_orm,
        "list_by_document_version",
        lambda document_id, version, token: [job],
    )
    monkeypatch.setattr(poll.task_orm, "begin_merge", lambda *args: True)
    monkeypatch.setattr(poll.task_orm, "get", lambda *args: task)
    monkeypatch.setattr(poll.document_orm, "get", lambda document_id: document)

    def unexpected(*args):
        pytest.fail("旧版本结果不应继续合并或切分")

    def fail(task_id, message, now, token=None):
        failures.append((task_id, token))
        task.status = DocumentTaskStatus.FAILED
        return True

    monkeypatch.setattr(poll.task_orm, "fail", fail)
    monkeypatch.setattr(poll.document_orm, "mark_failed", unexpected)
    monkeypatch.setattr(poll, "DocumentParsingService", unexpected)
    monkeypatch.setattr(poll, "DocumentChunkingService", unexpected)
    download_job(job.id, _Mineru(), _Reader(), _now())

    assert task.status == DocumentTaskStatus.FAILED
    assert failures == [("task-1", "merge-lease")]
    assert document.parse_version == version
    assert document.status == DocumentStatus.READY
    assert document.parsed_md_key == "current.md"


def test_apply_failure_marks_job_and_document(monkeypatch):
    now = _now()
    marked = []
    monkeypatch.setattr(
        poll.parse_job_orm,
        "set_status",
        lambda job_id, status, moment, next_poll_at, error: marked.append(status),
    )
    monkeypatch.setattr(poll.task_orm, "fail", lambda *args: marked.append("task") or True)
    monkeypatch.setattr(
        poll.document_orm,
        "mark_failed",
        lambda *args, **kwargs: marked.append("document"),
    )
    apply_plan(
        _job(attempts=3), plan_status(_job(attempts=3), "failed", "坏了", now), now
    )
    assert marked == [ParseJobStatus.FAILED, "task", "document"]


@pytest.mark.parametrize("action", ["poll", "download", "resubmit", "merge"])
def test_old_mineru_claim_is_ignored_even_when_task_has_a_new_token(monkeypatch, action):
    job = _job(lease_token="original-claim")
    monkeypatch.setattr(poll.parse_job_orm, "get", lambda *args: job)
    monkeypatch.setattr(poll.task_orm, "get", lambda *args: _task())
    tokens = []

    def renew(task_id, token, now, stages):
        tokens.append(token)
        return token == "merge-lease"

    def unexpected(*args, **kwargs):
        pytest.fail("旧批次不能访问 MinerU 或修改任务和文档")

    monkeypatch.setattr(poll.task_orm, "renew", renew)
    monkeypatch.setattr(poll.task_orm, "fail", unexpected)
    monkeypatch.setattr(poll.task_orm, "begin_merge", unexpected)
    monkeypatch.setattr(poll.parse_job_orm, "set_status", unexpected)
    monkeypatch.setattr(poll.document_orm, "mark_failed", unexpected)
    mineru = _Mineru()
    mineru.status = unexpected
    mineru.download = unexpected
    mineru.submit = unexpected
    if action == "poll":
        poll_job(job, mineru, _now())
    elif action == "download":
        job.status = ParseJobStatus.DOWNLOADING
        download_job(job.id, mineru, _Reader(), _now())
    elif action == "resubmit":
        resubmit_job(job.id, mineru, _Reader(), _now())
    else:
        merge_ready(job, _Reader(), _now())
    assert tokens == ["original-claim"]


def test_lease_replaced_during_mineru_status_cannot_apply_result(monkeypatch):
    from contextlib import contextmanager

    from model.document_task.lease import LeaseLost

    owner = ["merge-lease"]
    mineru = _Mineru()

    def status(job_id):
        owner[0] = "new-claim"
        return MinerUStatus(state="failed", error="late error")

    @contextmanager
    def guard(task_id, token, stages):
        if token != owner[0]:
            raise LeaseLost(task_id)
        yield

    def unexpected(*args, **kwargs):
        pytest.fail("远程查询返回时已失效，不能写分段、任务或文档")

    mineru.status = status
    monkeypatch.setattr(poll.task_orm, "lease_guard", guard)
    monkeypatch.setattr(poll.parse_job_orm, "set_status", unexpected)
    monkeypatch.setattr(poll.task_orm, "fail", unexpected)
    monkeypatch.setattr(poll.document_orm, "mark_failed", unexpected)
    poll_job(_job(attempts=3), mineru, _now())
