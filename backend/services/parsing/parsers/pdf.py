# -*- coding: utf-8 -*-
"""PDF 解析器。"""

import tempfile
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from uuid import uuid4

from model.parse_job import orm as parse_job_orm
from model.parse_job.model import ParseJob
from model.parse_job.model import ParseJobStatus
from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.mineru import MinerUClient
from services.parsing.models import ParseInput
from services.parsing.models import ParseSubmission
from services.parsing.models import SubmittedPart
from services.parsing.parsers.base import BaseParser
from services.parsing.parsers.pdf_split import PdfSegment
from services.parsing.parsers.pdf_split import split_pdf
from services.parsing.ports import ObjectReader
from services.task_runtime.policy import MINERU_POLL_SECONDS

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
        """提交一段。成功后立即写入 parse_jobs，已有远端任务的段不再提交。"""
        saved = _saved_part(source, index)
        if saved is not None:
            return saved
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
        part = SubmittedPart(
            file_id=job.file_id,
            job_id=job.job_id,
            part_index=index,
            page_start=segment.page_start,
            page_end=segment.page_end,
            source_key=key,
        )
        _store_part(source, part)
        return part


def _saved_part(source: ParseInput, index: int) -> SubmittedPart | None:
    """这一段已经交给 MinerU 时，直接用库里的任务号。"""
    if not source.task_id or not source.lease_token:
        return None
    existing = parse_job_orm.find_part(source.request.document_id, source.request.version, index)
    if existing is None or not existing.mineru_job_id:
        return None
    return SubmittedPart(
        file_id=existing.mineru_file_id or "",
        job_id=existing.mineru_job_id,
        part_index=index,
        page_start=existing.page_start,
        page_end=existing.page_end,
        source_key=existing.source_key,
    )


def _store_part(source: ParseInput, part: SubmittedPart) -> None:
    """一段提交成功就落库。没有任务上下文时只返回提交结果。"""
    if not source.task_id or not source.lease_token:
        return
    now = datetime.now(timezone.utc)
    parse_job_orm.insert(
        ParseJob(
            id=uuid4().hex,
            document_id=source.request.document_id,
            task_id=source.task_id,
            version=source.request.version,
            lease_token=source.lease_token,
            part_index=part.part_index,
            page_start=part.page_start,
            page_end=part.page_end,
            source_key=part.source_key,
            mineru_file_id=part.file_id,
            mineru_job_id=part.job_id,
            status=ParseJobStatus.SUBMITTED,
            attempts=1,
            next_poll_at=now + timedelta(seconds=MINERU_POLL_SECONDS),
            error_message=None,
            result_key=None,
            created_at=now,
            updated_at=now,
        )
    )


def _pdf_name(filename: str) -> str:
    """只用文件名本身，避免临时路径跑出目录。"""
    name = Path(filename).name
    if name in {"", ".", ".."}:
        return "document.pdf"
    return name
