# -*- coding: utf-8 -*-
"""PDF 解析器。"""

import tempfile
from pathlib import Path

from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.mineru import MinerUClient
from services.parsing.models import ParseInput
from services.parsing.models import ParseSubmission
from services.parsing.models import SubmittedPart
from services.parsing.parsers.base import BaseParser
from services.parsing.parsers.pdf_split import PdfSegment
from services.parsing.parsers.pdf_split import split_pdf
from services.parsing.ports import ObjectReader

_MAX_PAGES = 180
_MAX_BYTES = 180 * 1024 * 1024


class PdfParser(BaseParser):
    """按 180 页和 180MiB 拆开 PDF，再逐段交给 MinerU。"""

    file_types = ("pdf",)

    def __init__(
        self,
        mineru: MinerUClient | None = None,
        reader: ObjectReader | None = None,
        max_pages: int = _MAX_PAGES,
        max_bytes: int = _MAX_BYTES,
    ) -> None:
        self._mineru = mineru or MinerUClient()
        self._reader = reader or StorageObjectReader()
        self._max_pages = max_pages
        self._max_bytes = max_bytes

    def _parse(self, source: ParseInput) -> ParseSubmission:
        filename = _pdf_name(source.request.filename)
        with tempfile.TemporaryDirectory(prefix="pdf-split-") as tmp:
            root = Path(tmp)
            source_path = root / filename
            self._reader.download_object(source.request.object_key, source_path)
            segments = split_pdf(
                source_path,
                root / "parts",
                max_pages=self._max_pages,
                max_bytes=self._max_bytes,
            )
            return ParseSubmission(
                parts=tuple(
                    self._submit(source, filename, source_path, index, segment)
                    for index, segment in enumerate(segments, start=1)
                )
            )

    def _submit(
        self,
        source: ParseInput,
        filename: str,
        source_path: Path,
        index: int,
        segment: PdfSegment,
    ) -> SubmittedPart:
        """提交一段。拆出来的文件先写入对象存储，便于以后重试。"""
        if segment.path == source_path:
            key = source.request.object_key
            part_name = filename
        else:
            key = (
                f"documents/{source.request.document_id}"
                f"/parse/{source.request.version}/parts/{index}/source.pdf"
            )
            part_name = f"{Path(filename).stem}-part-{index}.pdf"
            self._reader.upload_file(
                key,
                segment.path,
                "application/pdf",
            )
        job = self._mineru.submit(segment.path, part_name, segment.sha256)
        return SubmittedPart(
            file_id=job.file_id,
            job_id=job.job_id,
            part_index=index,
            page_start=segment.page_start,
            page_end=segment.page_end,
            source_key=key,
        )


def _pdf_name(filename: str) -> str:
    """只用文件名本身，避免临时路径跑出目录。"""
    name = Path(filename).name
    if name in {"", ".", ".."}:
        return "document.pdf"
    return name
