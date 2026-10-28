# -*- coding: utf-8 -*-
"""解析任务在结果齐备后才上传。"""

import importlib
from datetime import datetime
from datetime import timezone

import pytest

from model.document.model import Document
from model.document.model import DocumentStatus
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from model.document_task.model import DocumentTaskStatus
from services.common.exceptions import FeatureNotReady
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


def _prepare(monkeypatch):
    monkeypatch.setattr(worker.task_orm, "claim", lambda task_id, now: _task())
    monkeypatch.setattr(worker.document_orm, "get", lambda document_id: _document())


def test_submitted_parse_does_not_upload(monkeypatch):
    _prepare(monkeypatch)
    progress = []
    monkeypatch.setattr(
        worker.task_orm,
        "update_progress",
        lambda task_id, stage, percent, step, now: progress.append(step),
    )
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "parse",
        lambda self, document, version: SubmittedParse(),
    )
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "upload",
        lambda self, parsed: (_ for _ in ()).throw(AssertionError("不应上传")),
    )
    monkeypatch.setattr(
        worker.parse_job_orm,
        "insert",
        lambda job: (_ for _ in ()).throw(AssertionError("不应写入分段")),
    )

    worker.parse_document.run("task-1")

    assert progress == ["分段已提交"]


def test_submitted_parse_records_job(monkeypatch):
    _prepare(monkeypatch)
    inserted = []
    progress = []
    part = SubmittedPart(
        file_id="file-1",
        job_id="job-1",
        part_index=1,
        page_start=1,
        page_end=0,
        source_key="documents/doc-1/demo.pdf",
    )
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "parse",
        lambda self, document, version: SubmittedParse(parts=(part,)),
    )
    monkeypatch.setattr(
        worker.parse_job_orm,
        "insert",
        lambda job: inserted.append(job),
    )
    monkeypatch.setattr(
        worker.task_orm,
        "update_progress",
        lambda task_id, stage, percent, step, now: progress.append(step),
    )
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "upload",
        lambda self, parsed: (_ for _ in ()).throw(AssertionError("不应上传")),
    )

    worker.parse_document.run("task-1")

    assert progress == ["分段已提交"]
    assert inserted[0].mineru_file_id == "file-1"
    assert inserted[0].mineru_job_id == "job-1"
    assert inserted[0].status.value == "SUBMITTED"
    assert inserted[0].task_id == "task-1"
    assert inserted[0].source_key == "documents/doc-1/demo.pdf"
    assert inserted[0].next_poll_at is not None


@pytest.mark.parametrize(
    ("error", "chunk_result"),
    [
        (None, True),
        (None, False),
        (RuntimeError("切分失败"), None),
        (FeatureNotReady("切分未就绪"), None),
    ],
)
def test_finished_parse_uploads_and_persists(monkeypatch, error, chunk_result):
    _prepare(monkeypatch)
    calls = []
    stored = StoredParseResult("parsed.md", "parsed.json")

    class _Chunking:
        def run(self, document, version, md_key, json_key):
            calls.append(("chunk", document.id, version, md_key, json_key))
            if error is not None:
                raise error
            return chunk_result

    monkeypatch.setattr(
        worker.task_orm,
        "fail",
        lambda task_id, message, now: calls.append(("fail", message)),
    )
    monkeypatch.setattr(
        worker.document_orm,
        "mark_failed",
        lambda document_id, message, now, **kwargs: calls.append(
            ("document_failed", kwargs["expected_version"])
        ),
    )
    monkeypatch.setattr(worker, "DocumentChunkingService", _Chunking, raising=False)
    monkeypatch.setattr(
        worker.task_orm,
        "update_progress",
        lambda task_id, stage, percent, step, now: calls.append((stage, percent)),
    )
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "parse",
        lambda self, document, version: FinishedParse(_parsed()),
    )
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "upload",
        lambda self, parsed: calls.append("upload") or stored,
    )
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "persist",
        lambda self, parsed, stored: calls.append(("persist", stored)),
    )
    monkeypatch.setattr(
        worker.task_orm,
        "complete",
        lambda task_id, step, now: calls.append(("complete", step)),
    )

    if error is None:
        worker.parse_document.run("task-1")
    else:
        with pytest.raises(type(error), match=str(error)):
            worker.parse_document.run("task-1")

    expected_end = [("complete", "解析与切分完成")]
    if error is not None:
        expected_end = [("fail", str(error)), ("document_failed", 1)]
    elif chunk_result is False:
        expected_end = []
    assert (
        calls
        == [
            "upload",
            ("persist", stored),
            (DocumentTaskStage.CHUNKING, 85),
            ("chunk", "doc-1", 1, "parsed.md", "parsed.json"),
        ]
        + expected_end
    )


@pytest.mark.parametrize("file_type", ["csv", "xlsx"])
def test_structured_files_persist_json_without_chunking(monkeypatch, file_type):
    _prepare(monkeypatch)
    document = _document()
    document.file_type = file_type
    calls = []
    stored = StoredParseResult("parsed.md", "parsed.json")
    monkeypatch.setattr(worker.document_orm, "get", lambda document_id: document)
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "parse",
        lambda self, document, version: FinishedParse(_parsed()),
    )
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "upload",
        lambda self, parsed: calls.append("upload") or stored,
    )
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "persist",
        lambda self, parsed, result: calls.append(("persist", result.json_key)),
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("结构化数据不应进入文本切分或提前标记就绪")

    monkeypatch.setattr(worker.task_orm, "fail", lambda *args: None)
    monkeypatch.setattr(
        worker.document_orm, "mark_failed", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(worker, "DocumentChunkingService", unexpected)
    monkeypatch.setattr(worker.task_orm, "update_progress", unexpected)
    monkeypatch.setattr(worker.document_orm, "mark_ready", unexpected)
    monkeypatch.setattr(
        worker.task_orm,
        "complete",
        lambda task_id, step, now: calls.append(("complete", step)),
    )
    worker.parse_document.run("task-1")
    assert calls == [
        "upload",
        ("persist", "parsed.json"),
        ("complete", "解析完成，待结构化入库"),
    ]


def test_recovered_parse_resumes_stored_artifacts(monkeypatch):
    _prepare(monkeypatch)
    document = _document()
    document.parsed_md_key = "stored.md"
    document.parsed_json_key = "stored.json"
    task = _task()
    task.stage = DocumentTaskStage.CHUNKING
    monkeypatch.setattr(worker.task_orm, "claim", lambda *args: task)
    monkeypatch.setattr(worker.document_orm, "get", lambda document_id: document)
    calls = []

    def unexpected(*args):
        pytest.fail("恢复任务不应重新解析已有产物")

    class _Chunking:
        def run(self, document, version, md_key, json_key):
            calls.append((version, md_key, json_key))
            return True

    monkeypatch.setattr(worker, "DocumentParsingService", unexpected)
    monkeypatch.setattr(worker, "DocumentChunkingService", _Chunking)
    monkeypatch.setattr(worker.task_orm, "update_progress", lambda *args: None)
    monkeypatch.setattr(worker.task_orm, "complete", lambda *args: calls.append("done"))
    worker.parse_document.run("task-1")
    assert calls == [(1, "stored.md", "stored.json"), "done"]


def test_stale_parse_does_not_run_services(monkeypatch):
    _prepare(monkeypatch)
    document = _document()
    document.parse_version = 2
    monkeypatch.setattr(worker.document_orm, "get", lambda document_id: document)

    def unexpected(*args):
        pytest.fail("旧版本任务不应继续解析")

    monkeypatch.setattr(worker, "DocumentParsingService", unexpected)
    monkeypatch.setattr(worker, "DocumentChunkingService", unexpected)
    worker.parse_document.run("task-1")
