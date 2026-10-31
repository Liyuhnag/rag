# -*- coding: utf-8 -*-
"""PDF 按页和大小拆分后再提交。"""

import hashlib
from pathlib import Path

import pymupdf
import pytest

from services.parsing.errors import ParseFailed
from services.parsing.mineru import MinerUJob
from services.parsing.models import ParseInput
from services.parsing.models import ParseRequest
from services.parsing.parsers.pdf import PdfParser
from services.parsing.parsers.pdf_split import split_pdf


class _Reader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.downloads = 0
        self.writes: list[tuple[str, bytes]] = []

    def read_object(self, key: str) -> bytes:
        raise AssertionError("PDF 拆分应下载到文件")

    def download_object(self, key: str, path: Path) -> None:
        self.downloads += 1
        path.write_bytes(self.content)

    def write_object(self, key: str, content: bytes, content_type: str) -> None:
        raise AssertionError("PDF 分段必须通过文件上传")

    def upload_file(self, key: str, path: Path, content_type: str) -> None:
        assert content_type == "application/pdf"
        with path.open("rb") as handle:
            self.writes.append((key, handle.read()))


class _MinerU:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str, str]] = []

    def submit(self, file_path: Path, filename: str, sha256: str) -> MinerUJob:
        content = file_path.read_bytes()
        self.calls.append((content, filename, sha256))
        return MinerUJob(file_id=f"file-{len(self.calls)}", job_id=f"job-{len(self.calls)}")


def _pdf(page_count: int, text: str = "正文") -> bytes:
    document = pymupdf.open()
    for index in range(page_count):
        page = document.new_page()
        page.insert_text((72, 72), f"{text}-{index}")
    payload = document.tobytes()
    document.close()
    return payload


def _source(size_bytes: int) -> ParseInput:
    return ParseInput(
        request=ParseRequest(
            document_id="doc-1",
            version=3,
            object_key="documents/doc-1/source/demo.pdf",
            file_type="pdf",
            filename="目录/demo.pdf",
            size_bytes=size_bytes,
        )
    )


def test_small_pdf_is_submitted_as_one_part(tmp_path):
    content = _pdf(2)
    reader = _Reader(content)
    mineru = _MinerU()

    submitted = PdfParser(mineru=mineru, reader=reader).parse(_source(len(content)))

    assert reader.downloads == 1
    assert reader.writes == []
    assert len(submitted.parts) == 1
    part = submitted.parts[0]
    assert part.page_start == 1
    assert part.page_end == 2
    assert part.part_index == 1
    assert part.source_key == "documents/doc-1/source/demo.pdf"
    assert mineru.calls == [
        (content, "demo.pdf", hashlib.sha256(content).hexdigest())
    ]


def test_pdf_over_page_limit_is_split_and_uploaded():
    content = _pdf(5)
    reader = _Reader(content)
    mineru = _MinerU()
    parser = PdfParser(mineru=mineru, reader=reader, max_pages=2)

    submitted = parser.parse(_source(len(content)))

    assert [part.page_start for part in submitted.parts] == [1, 3, 5]
    assert [part.page_end for part in submitted.parts] == [2, 4, 5]
    assert [part.part_index for part in submitted.parts] == [1, 2, 3]
    assert [part.job_id for part in submitted.parts] == ["job-1", "job-2", "job-3"]
    assert [name for _, name, _ in mineru.calls] == [
        "demo-part-1.pdf",
        "demo-part-2.pdf",
        "demo-part-3.pdf",
    ]
    assert [key for key, _ in reader.writes] == [
        "documents/doc-1/parse/3/parts/1/source.pdf",
        "documents/doc-1/parse/3/parts/2/source.pdf",
        "documents/doc-1/parse/3/parts/3/source.pdf",
    ]
    opened = pymupdf.open(stream=reader.writes[0][1], filetype="pdf")
    assert opened.page_count == 2
    opened.close()


def test_single_page_over_limit_is_rejected():
    content = _pdf(1)
    parser = PdfParser(mineru=_MinerU(), reader=_Reader(content), max_bytes=len(content) - 1)

    with pytest.raises(ParseFailed, match="单页超过"):
        parser.parse(_source(len(content)))


def test_encrypted_pdf_is_rejected(tmp_path):
    path = tmp_path / "secret.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    document.close()

    with pytest.raises(ParseFailed, match="已加密"):
        split_pdf(path, tmp_path / "parts")


def test_corrupt_pdf_is_rejected(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.4\ngarbage")

    with pytest.raises(ParseFailed, match="已损坏"):
        split_pdf(path, tmp_path / "parts")


def test_oversized_span_is_halved(tmp_path):
    content = _pdf(4, text="字" * 2000)
    source = tmp_path / "large.pdf"
    source.write_bytes(content)
    three_pages = _extract(source, 0, 3)
    one_page = _extract(source, 0, 1)
    max_bytes = len(three_pages) - 1
    assert len(one_page) <= max_bytes

    segments = split_pdf(source, tmp_path / "parts", max_bytes=max_bytes)

    assert [(item.page_start, item.page_end) for item in segments] == [
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
    ]
    assert all(item.path.stat().st_size <= max_bytes for item in segments)


def _extract(source: Path, start: int, end: int) -> bytes:
    document = pymupdf.open(source)
    part = pymupdf.open()
    part.insert_pdf(document, from_page=start, to_page=end - 1)
    payload = part.tobytes()
    part.close()
    document.close()
    return payload
