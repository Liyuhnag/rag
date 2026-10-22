# -*- coding: utf-8 -*-
"""解析任务在结果齐备后才上传。"""

import importlib
from datetime import datetime
from datetime import timezone

from model.document.model import Document
from model.document.model import DocumentStatus
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from model.document_task.model import DocumentTaskStatus
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedDocument
from services.parsing.service import FinishedParse
from services.parsing.service import SubmittedParse

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

    worker.parse_document.run("task-1")

    assert progress == ["分段已提交"]


def test_finished_parse_uploads_and_persists(monkeypatch):
    _prepare(monkeypatch)
    calls = []
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "parse",
        lambda self, document, version: FinishedParse(_parsed()),
    )
    monkeypatch.setattr(
        worker.DocumentParsingService,
        "upload",
        lambda self, parsed: calls.append("upload") or "stored",
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

    worker.parse_document.run("task-1")

    assert calls == ["upload", ("persist", "stored"), ("complete", "解析完成")]
