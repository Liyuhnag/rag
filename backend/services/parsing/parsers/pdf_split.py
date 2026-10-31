# -*- coding: utf-8 -*-
"""按页数和大小把 PDF 拆成 MinerU 能接收的分段。"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from services.parsing.errors import ParseFailed

_MAX_PAGES = 180
_MAX_BYTES = 180 * 1024 * 1024


@dataclass(frozen=True)
class PdfSegment:
    """一个已落盘的 PDF 分段。页码从 1 开始，并且包含结束页。"""

    path: Path
    page_start: int
    page_end: int
    sha256: str


def split_pdf(
    source: Path,
    output_dir: Path,
    *,
    max_pages: int = _MAX_PAGES,
    max_bytes: int = _MAX_BYTES,
) -> list[PdfSegment]:
    """不超过上限时返回原文件；否则按页切开，超大的段再对半拆。"""
    try:
        document = pymupdf.open(source)
    except Exception as exc:
        raise ParseFailed("PDF 已损坏") from exc
    try:
        if document.needs_pass and not document.authenticate(""):
            raise ParseFailed("PDF 已加密")
        page_count = document.page_count
        size = source.stat().st_size
        if page_count <= 0 or size <= 0:
            raise ParseFailed("PDF 已损坏")
        if page_count <= max_pages and size <= max_bytes:
            return [_segment(source, 1, page_count)]
        if page_count == 1:
            raise ParseFailed("PDF 单页超过 180MiB，请压缩或拆分源文件")
        output_dir.mkdir(parents=True, exist_ok=True)
        pages_per_part = _pages_per_part(
            size,
            page_count,
            max_pages,
            max_bytes,
        )
        return _split_span(
            document,
            0,
            page_count,
            pages_per_part,
            output_dir,
            max_bytes,
        )
    finally:
        document.close()


def _pages_per_part(
    file_size: int,
    page_count: int,
    max_pages: int,
    max_bytes: int,
) -> int:
    """按平均页大小估算一段能放多少页。"""
    pages_by_size = (max_bytes * page_count) // file_size
    return max(1, min(max_pages, pages_by_size, page_count))


def _split_span(
    document: pymupdf.Document,
    start: int,
    end: int,
    pages_per_part: int,
    output_dir: Path,
    max_bytes: int,
) -> list[PdfSegment]:
    """拆开 [start, end)。页码参数是 0 起始，end 不包含。"""
    segments: list[PdfSegment] = []
    cursor = start
    while cursor < end:
        chunk = min(pages_per_part, end - cursor)
        stop = cursor + chunk
        path = _write_pages(document, cursor, stop, output_dir)
        if path.stat().st_size > max_bytes:
            path.unlink()
            if chunk == 1:
                raise ParseFailed("PDF 单页超过 180MiB，请压缩或拆分源文件")
            half = max(1, chunk // 2)
            segments.extend(
                _split_span(
                    document,
                    cursor,
                    stop,
                    half,   
                    output_dir,
                    max_bytes,
                )
            )
        else:
            segments.append(_segment(path, cursor + 1, stop))
        cursor = stop
    return segments


def _write_pages(
    document: pymupdf.Document,
    start: int,
    end: int,
    output_dir: Path,
) -> Path:
    """把 [start, end) 写成一个新 PDF。"""
    target = output_dir / f"pages-{start + 1}-{end}.pdf"
    part = pymupdf.open()
    try:
        part.insert_pdf(document, from_page=start, to_page=end - 1)
        part.save(target)
    except Exception as exc:
        raise ParseFailed("PDF 已损坏") from exc
    finally:
        part.close()
    return target


def _segment(path: Path, page_start: int, page_end: int) -> PdfSegment:
    """记录分段文件和页码。"""
    return PdfSegment(
        path=path,
        page_start=page_start,
        page_end=page_end,
        sha256=_sha256(path),
    )


def _sha256(path: Path) -> str:
    """按块计算文件摘要。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()
