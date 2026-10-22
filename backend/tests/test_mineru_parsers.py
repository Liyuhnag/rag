# -*- coding: utf-8 -*-
"""PDF 和 Word 解析器把未超限的文件交给 MinerU。"""

import hashlib
from datetime import datetime
from datetime import timezone

import pytest

import services.parsing.service as parsing_service

from model.document.model import Document
from model.document.model import DocumentStatus
from services.parsing.mineru import MinerUJob
from services.parsing.errors import ParseFailed
from services.parsing.models import ParseInput
from services.parsing.models import ParseRequest
from services.parsing.models import ParseSubmission
from services.parsing.models import ParsedDocument
from services.parsing.parsers.doc import DocParser
from services.parsing.parsers.docx import DocxParser
from services.parsing.parsers.pdf import PdfParser
from services.parsing.service import DocumentParsingService
from services.parsing.service import FinishedParse
from services.parsing.service import SubmittedParse


class _Reader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.reads = 0

    def read_object(self, key: str) -> bytes:
        self.reads += 1
        return self.content

    def write_object(self, key: str, content: bytes, content_type: str) -> None:
        raise AssertionError("解析时不应写对象")


class _MinerU:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str, str]] = []

    def submit(self, file_path, filename: str, sha256: str) -> MinerUJob:
        self.calls.append((file_path.read_bytes(), filename, sha256))
        return MinerUJob(file_id="file-1", job_id="job-1")


def _source(file_type: str, size_bytes: int) -> ParseInput:
    return ParseInput(
        request=ParseRequest(
            document_id="doc-1",
            version=1,
            object_key=f"documents/doc-1/source/demo.{file_type}",
            file_type=file_type,
            filename=f"目录/demo.{file_type}",
            size_bytes=size_bytes,
        )
    )


@pytest.mark.parametrize(
    ("parser_cls", "file_type", "message"),
    [
        (PdfParser, "pdf", "需要先拆分"),
        (DocParser, "doc", "不能直接提交"),
        (DocxParser, "docx", "不能直接提交"),
    ],
)
def test_recorded_size_is_rejected_before_reading(parser_cls, file_type, message):
    reader = _Reader(b"tiny")
    mineru = _MinerU()
    parser = parser_cls(mineru=mineru, reader=reader, max_bytes=4)

    with pytest.raises(ParseFailed, match=message):
        parser.parse(_source(file_type, 5))

    assert reader.reads == 0
    assert mineru.calls == []


@pytest.mark.parametrize(
    ("parser_cls", "file_type"),
    [
        (PdfParser, "pdf"),
        (DocParser, "doc"),
        (DocxParser, "docx"),
    ],
)
def test_actual_size_is_rejected_after_reading(parser_cls, file_type):
    reader = _Reader(b"12345")
    mineru = _MinerU()
    parser = parser_cls(mineru=mineru, reader=reader, max_bytes=4)

    with pytest.raises(ParseFailed):
        parser.parse(_source(file_type, 4))

    assert reader.reads == 1
    assert mineru.calls == []


@pytest.mark.parametrize(
    ("parser_cls", "file_type"),
    [
        (PdfParser, "pdf"),
        (DocParser, "doc"),
        (DocxParser, "docx"),
    ],
)
def test_file_within_limit_is_submitted(parser_cls, file_type):
    content = b"1234"
    reader = _Reader(content)
    mineru = _MinerU()
    parser = parser_cls(mineru=mineru, reader=reader, max_bytes=4)

    submitted = parser.parse(_source(file_type, 4))

    assert isinstance(submitted, ParseSubmission)
    assert submitted.parts[0].file_id == "file-1"
    assert submitted.parts[0].job_id == "job-1"
    assert submitted.parts[0].page_end == 0
    assert mineru.calls == [
        (content, f"demo.{file_type}", hashlib.sha256(content).hexdigest())
    ]


def test_service_turns_submission_into_submitted_parse(monkeypatch):
    class _Parser:
        def parse(self, source: ParseInput) -> ParseSubmission:
            return PdfParser(
                mineru=_MinerU(),
                reader=_Reader(b"pdf"),
                max_bytes=4,
            ).parse(source)

    class _Registry:
        def get(self, file_type: str) -> _Parser:
            return _Parser()

    monkeypatch.setattr(parsing_service, "build_default_registry", lambda: _Registry())
    document = _document("pdf", 3)

    outcome = DocumentParsingService().parse(document, 2)

    assert isinstance(outcome, SubmittedParse)
    assert outcome.parts[0].job_id == "job-1"
    assert outcome.parts[0].source_key == document.object_key


def test_service_keeps_finished_parse(monkeypatch):
    parsed = ParsedDocument(
        request=ParseRequest(
            document_id="doc-1",
            version=1,
            object_key="documents/doc-1/demo.txt",
            file_type="txt",
            filename="demo.txt",
            size_bytes=3,
        ),
        title="demo",
    )

    class _Parser:
        def parse(self, source: ParseInput) -> ParsedDocument:
            return parsed

    class _Registry:
        def get(self, file_type: str) -> _Parser:
            return _Parser()

    monkeypatch.setattr(parsing_service, "build_default_registry", lambda: _Registry())

    outcome = DocumentParsingService().parse(_document("txt", 3), 1)

    assert isinstance(outcome, FinishedParse)
    assert outcome.parsed is parsed


def _document(file_type: str, size_bytes: int) -> Document:
    now = datetime.now(timezone.utc)
    return Document(
        id="doc-1",
        name=f"demo.{file_type}",
        file_type=file_type,
        size_bytes=size_bytes,
        chunk_count=0,
        status=DocumentStatus.UPLOADED,
        uploaded_at=now,
        updated_at=now,
        object_key=f"documents/doc-1/demo.{file_type}",
        file_url="",
        previewable=False,
        preview_url=None,
    )
