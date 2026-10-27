# -*- coding: utf-8 -*-
"""MinerU 分段轮询、重新提交和下载合并。"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from model.document.model import Document
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
        lambda task_id, message, moment: failed.append(message),
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
        lambda document_id, version: [job, sibling],
    )
    monkeypatch.setattr(
        poll.task_orm,
        "begin_merge",
        lambda task_id, moment: merged.append(task_id) or True,
    )
    download_job(job.id, _Mineru(body=b"PK"), reader, now)
    assert saved == ["documents/doc-1/parse/2/parts/1/result.zip"]
    assert reader.written[0][1] == "application/zip"
    assert merged == []


def test_download_merges_when_every_part_has_a_zip(monkeypatch):
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
        def __init__(self, reader):
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
        lambda document_id, version: [first, second],
    )
    monkeypatch.setattr(poll.task_orm, "begin_merge", lambda task_id, moment: True)
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
        lambda task_id, step, moment: completed.append(step),
    )
    monkeypatch.setattr(poll, "DocumentParsingService", _Service)
    download_job(first.id, _Mineru(), reader, now)
    assert collected == [("documents/doc-1/demo.pdf", [1, 2])]
    assert recorded[0] == "documents/doc-1/parsed/2/merged.md"
    assert completed == ["解析完成"]


def test_second_merge_claim_does_not_collect(monkeypatch):
    job = _job(
        status=ParseJobStatus.DOWNLOADING,
        result_key="documents/doc-1/parse/2/parts/1/result.zip",
    )
    called = []
    monkeypatch.setattr(
        poll.parse_job_orm,
        "list_by_document_version",
        lambda document_id, version: [job],
    )
    monkeypatch.setattr(poll.task_orm, "begin_merge", lambda task_id, moment: False)
    monkeypatch.setattr(
        poll,
        "DocumentParsingService",
        lambda reader: called.append(reader),
    )
    merge_ready(job, _Reader({job.result_key: b"zip"}), _now())
    assert called == []


def test_apply_failure_marks_job_and_document(monkeypatch):
    now = _now()
    marked = []
    monkeypatch.setattr(
        poll.parse_job_orm,
        "set_status",
        lambda job_id, status, moment, next_poll_at, error: marked.append(status),
    )
    monkeypatch.setattr(poll.task_orm, "fail", lambda *args: marked.append("task"))
    monkeypatch.setattr(
        poll.document_orm,
        "mark_failed",
        lambda *args, **kwargs: marked.append("document"),
    )
    apply_plan(
        _job(attempts=3), plan_status(_job(attempts=3), "failed", "坏了", now), now
    )
    assert marked == [ParseJobStatus.FAILED, "task", "document"]
