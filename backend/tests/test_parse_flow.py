# -*- coding: utf-8 -*-
"""解析任务在结果齐备后才上传。"""

import importlib
from datetime import datetime
from datetime import timezone

import pytest

pytestmark = pytest.mark.usefixtures("parse_lease")

from model.document.model import Document
from model.document.model import DocumentStatus
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from model.document_task.model import DocumentTaskStatus
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedDocument
from services.parsing.models import SubmittedPart
from services.parsing.service import FinishedParse
from services.parsing.service import SubmittedParse
from services.parsing.service import StoredParseResult

worker = importlib.import_module("task.parse_document")


def _document() -> Document:
    now = datetime.now(timezone.utc)
    return Document(
        id="doc-1",
        name="demo.txt",
        file_type="txt",
        size_bytes=4,
        chunk_count=0,
        status=DocumentStatus.UPLOADED,
        uploaded_at=now,
        updated_at=now,
        object_key="documents/doc-1/demo.txt",
        file_url="",
        previewable=False,
        preview_url=None,
        parse_version=1,
    )


def _task() -> DocumentTask:
    return DocumentTask(
        id="task-1",
        document_id="doc-1",
        version=1,
        status=DocumentTaskStatus.RUNNING,
        stage=DocumentTaskStage.PARSING,
        progress=0,
        step="解析中",
        lease_token="parse-lease",
    )


def _parsed() -> ParsedDocument:
    return ParsedDocument(
        request=ParseRequest(
            document_id="doc-1",
            version=1,
            object_key="documents/doc-1/demo.txt",
            file_type="txt",
            filename="demo.txt",
            size_bytes=4,
        ),
        title="demo",
    )


def _prepare(monkeypatch, stage=DocumentTaskStage.PARSING):
    from services.task_runtime import lifecycle

    task = _task()
    task.stage = stage
    document = _document()
    calls = []

    def claim(task_id, now, stages):
        assert DocumentTaskStage.INDEXING not in stages
        task.status = DocumentTaskStatus.RUNNING
        task.lease_token = "parse-lease"
        return task

    def begin(*args):
        document.status = DocumentStatus.PROCESSING
        return True

    def handoff(task_id, token, target, progress, step, now, stages):
        task.status = DocumentTaskStatus.PENDING
        task.stage = target
        task.lease_token = None
        calls.append(("handoff", target))
        return True

    def failed(task_id, message, now, token):
        task.status = DocumentTaskStatus.FAILED
        task.error_message = message
        return True

    def superseded(task_id, message, now, token):
        task.status = DocumentTaskStatus.SUPERSEDED
        return True

    def document_failed(*args, **kwargs):
        document.status = DocumentStatus.FAILED

    def complete(*args):
        task.status = DocumentTaskStatus.COMPLETED
        task.stage = DocumentTaskStage.DONE
        return True

    monkeypatch.setattr(worker.task_orm, "claim", claim)
    monkeypatch.setattr(worker.document_orm, "get", lambda *args: document)
    monkeypatch.setattr(worker.document_orm, "begin_parse", begin)
    monkeypatch.setattr(worker.parse_job_orm, "adopt_task_jobs", lambda *args: [])
    monkeypatch.setattr(worker.task_orm, "advance", handoff)
    monkeypatch.setattr(worker.task_orm, "fail", failed)
    monkeypatch.setattr(worker.task_orm, "supersede", superseded)
    monkeypatch.setattr(worker.task_orm, "complete", complete)
    monkeypatch.setattr(worker.task_orm, "update_progress", lambda *args: True)
    monkeypatch.setattr(worker.document_orm, "mark_failed", document_failed)
    monkeypatch.setattr(lifecycle, "dispatch", lambda task_id, target: calls.append(("dispatch", target)))
    return task, document, calls


def test_submitted_parse_records_jobs_and_waits(monkeypatch):
    task, document, calls = _prepare(monkeypatch)
    part = SubmittedPart("file-1", "remote-1", 1, 1, 10, "source.pdf")
    monkeypatch.setattr(worker.DocumentParsingService, "parse", lambda *args: SubmittedParse(parts=(part,)))
    inserted = []
    monkeypatch.setattr(worker.parse_job_orm, "insert", inserted.append)
    worker.parse_document.run(task.id)
    assert task.status == "RUNNING"
    assert document.status == "PROCESSING"
    assert inserted[0].lease_token == "parse-lease"
    assert inserted[0].mineru_job_id == "remote-1"
    assert calls == []


@pytest.mark.parametrize("downloaded", [False, True])
def test_reclaimed_mineru_parts_are_reused_without_resubmission(monkeypatch, downloaded):
    from types import SimpleNamespace

    from model.parse_job.model import ParseJobStatus

    task, document, calls = _prepare(monkeypatch)
    document.file_type = "pdf"
    document.status = DocumentStatus.PROCESSING
    part = SimpleNamespace(
        id="existing-part",
        status=ParseJobStatus.DOWNLOADING if downloaded else ParseJobStatus.SUBMITTED,
        result_key="existing.zip" if downloaded else None,
        mineru_job_id="existing-remote-job",
    )
    adopted = []

    def adopt(task_id, document_id, version, token):
        adopted.append((task_id, document_id, version, token))
        return [part]

    monkeypatch.setattr(worker.parse_job_orm, "adopt_task_jobs", adopt)
    monkeypatch.setattr(
        worker, "DocumentParsingService",
        lambda *args, **kwargs: pytest.fail("已有分段不能重新解析或提交"),
    )
    worker.parse_document.run(task.id)
    assert adopted == [("task-1", "doc-1", 1, "parse-lease")]
    assert part.mineru_job_id == "existing-remote-job"
    assert document.status == "PROCESSING"
    if downloaded:
        assert (task.status, task.stage) == ("PENDING", "MERGING")
        assert calls == [("handoff", "MERGING"), ("dispatch", "MERGING")]
        assert part.result_key == "existing.zip"
    else:
        assert (task.status, task.stage) == ("RUNNING", "PARSING")
        assert calls == []


def test_local_parse_publishes_then_dispatches_independent_chunking(monkeypatch):
    task, document, calls = _prepare(monkeypatch)
    monkeypatch.setattr(worker.DocumentParsingService, "parse", lambda *args: FinishedParse(_parsed()))
    monkeypatch.setattr(worker.DocumentParsingService, "upload", lambda *args: StoredParseResult("md", "json"))

    def persist(*args):
        document.parsed_md_key = "md"
        document.parsed_json_key = "json"
        calls.append("persist")

    monkeypatch.setattr(worker.DocumentParsingService, "persist", persist)
    monkeypatch.setattr(worker, "DocumentChunkingService", lambda: pytest.fail("解析消息不执行切分"))
    worker.parse_document.run(task.id)
    assert task.status == "PENDING"
    assert task.stage == "CHUNKING"
    assert task.lease_token is None
    assert document.status == "PROCESSING"
    assert calls == ["persist", ("handoff", "CHUNKING"), ("dispatch", "CHUNKING")]


@pytest.mark.parametrize("file_type", ["txt", "csv", "xlsx"])
def test_chunking_reuses_artifacts_and_hands_off_or_completes(monkeypatch, file_type):
    task, document, calls = _prepare(monkeypatch, DocumentTaskStage.CHUNKING)
    document.file_type = file_type
    document.status = DocumentStatus.PROCESSING
    document.parsed_md_key, document.parsed_json_key = "md", "json"
    monkeypatch.setattr(worker, "DocumentParsingService", lambda *args: pytest.fail("不得重新解析"))

    class Service:
        def run(self, current, version, md_key, json_key):
            calls.append(("save", current.id, version, md_key, json_key))
            return True

    monkeypatch.setattr(worker, "DocumentChunkingService", Service)
    monkeypatch.setattr(worker, "StructuredSchemaService", Service)
    worker.parse_document.run(task.id)
    assert calls[0] == ("save", "doc-1", 1, "md", "json")
    if file_type == "txt":
        assert (task.status, task.stage) == ("PENDING", "INDEXING")
        assert calls[1:] == [("handoff", "INDEXING"), ("dispatch", "INDEXING")]
    else:
        assert (task.status, task.stage) == ("COMPLETED", "DONE")
        assert len(calls) == 1


def test_missing_chunking_input_rolls_back_to_parsing(monkeypatch):
    task, _, calls = _prepare(monkeypatch, DocumentTaskStage.CHUNKING)
    worker.parse_document.run(task.id)
    assert (task.status, task.stage) == ("PENDING", "PARSING")
    assert calls == [("handoff", "PARSING"), ("dispatch", "PARSING")]


@pytest.mark.parametrize("stage", [DocumentTaskStage.PARSING, DocumentTaskStage.MERGING, DocumentTaskStage.CHUNKING])
def test_stale_version_supersedes_task_without_modifying_document(monkeypatch, stage):
    task, document, calls = _prepare(monkeypatch, stage)
    document.parse_version = 2
    document.status = DocumentStatus.READY
    monkeypatch.setattr(worker, "DocumentParsingService", lambda *args: pytest.fail("过期任务不能执行"))
    worker.parse_document.run(task.id)
    assert task.status == "SUPERSEDED"
    assert task.stage == stage
    assert document.status == "READY"
    assert calls == []


@pytest.mark.parametrize("stage", [DocumentTaskStage.PARSING, DocumentTaskStage.CHUNKING, DocumentTaskStage.MERGING])
def test_business_error_fails_task_and_current_document(monkeypatch, stage):
    task, document, calls = _prepare(monkeypatch, stage)
    document.status = DocumentStatus.PROCESSING
    document.parsed_md_key, document.parsed_json_key = "md", "json"

    def unavailable(*args, **kwargs):
        raise RuntimeError("service unavailable")

    monkeypatch.setattr(worker.DocumentParsingService, "parse", unavailable)
    monkeypatch.setattr(worker, "run_merge", unavailable)

    class Service:
        run = unavailable

    monkeypatch.setattr(worker, "DocumentChunkingService", Service)
    with pytest.raises(RuntimeError, match="service unavailable"):
        worker.parse_document.run(task.id)
    assert (task.status, task.stage) == ("FAILED", stage)
    assert document.status == "FAILED"
    assert calls == []


def test_version_changed_before_begin_parse_is_superseded(monkeypatch):
    task, document, _ = _prepare(monkeypatch)
    monkeypatch.setattr(worker.document_orm, "begin_parse", lambda *args: False)
    monkeypatch.setattr(worker.DocumentParsingService, "parse", lambda *args: pytest.fail("不能解析"))
    worker.parse_document.run(task.id)
    assert task.status == "SUPERSEDED"
    assert document.status == "UPLOADED"


@pytest.mark.parametrize("lost_at", ["parse", "upload"])
def test_lost_lease_during_external_work_cannot_publish_or_fail(monkeypatch, lost_at):
    from contextlib import contextmanager
    from model.document_task.lease import LeaseLost

    task, document, calls = _prepare(monkeypatch)
    owner = ["parse-lease"]

    @contextmanager
    def guard(task_id, token, stages):
        if token != owner[0]:
            raise LeaseLost(task_id)
        yield

    def parse(*args):
        if lost_at == "parse":
            owner[0] = "new-worker"
        return FinishedParse(_parsed())

    def upload(*args):
        owner[0] = "new-worker"
        return StoredParseResult("md", "json")

    monkeypatch.setattr(worker.task_orm, "lease_guard", guard)
    monkeypatch.setattr(worker.task_orm, "owns_lease", lambda task_id, token, stages: token == owner[0])
    monkeypatch.setattr(worker.DocumentParsingService, "parse", parse)
    monkeypatch.setattr(worker.DocumentParsingService, "upload", upload)
    monkeypatch.setattr(worker.DocumentParsingService, "persist", lambda *args: pytest.fail("旧执行不能发布"))
    worker.parse_document.run(task.id)
    assert task.status == "RUNNING"
    assert document.status == "PROCESSING"
    assert calls == []
