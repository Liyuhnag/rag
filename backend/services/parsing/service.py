# -*- coding: utf-8 -*-
"""文档解析服务。

负责三件事：按文件类型解析、把解析产物上传到对象存储、把结果写入数据库。
读取源文件和具体格式处理由解析器完成。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path

from model.document import orm as document_orm
from model.document.model import Document
from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.collect import PartZip
from services.parsing.collect import collect_parts
from services.parsing.models import ParseInput
from services.parsing.models import ParseRequest
from services.parsing.models import ParseSubmission
from services.parsing.models import ParsedDocument
from services.parsing.models import SubmittedPart
from services.parsing.parsers.registry import build_default_registry
from services.parsing.ports import ObjectReader


@dataclass(frozen=True)
class StoredParseResult:
    """已上传的解析产物位置。"""

    md_key: str
    json_key: str
    manifest_key: str = ""
    part_results: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class FinishedParse:
    """解析已经完成，可以上传和落库。"""

    parsed: ParsedDocument


@dataclass(frozen=True)
class SubmittedParse:
    """分段已提交，等待外部解析结束。"""

    parts: tuple[SubmittedPart, ...] = ()


class DocumentParsingService:
    """解析文档，上传产物，并写入数据库。"""

    def __init__(
        self, reader: ObjectReader | None = None, attempt_id: str | None = None
    ) -> None:
        self._reader = reader or StorageObjectReader()
        self._attempt_id = attempt_id

    def parse(
        self,
        document: Document,
        version: int,
    ) -> FinishedParse | SubmittedParse:
        """按扩展名把文档交给对应解析器。"""
        request = ParseRequest(
            document_id=document.id,
            version=version,
            object_key=document.object_key,
            file_type=document.file_type,
            filename=document.name,
            size_bytes=document.size_bytes,
        )
        parser = build_default_registry(reader=self._reader).get(document.file_type)
        if hasattr(parser, "iter_blocks"):
            parsed = ParsedDocument(
                request=request,
                title=Path(document.name).stem,
                blocks=parser.iter_blocks(ParseInput(request)),
            )
            return FinishedParse(parsed)
        outcome = parser.parse(ParseInput(request=request))
        if isinstance(outcome, ParseSubmission):
            return SubmittedParse(parts=outcome.parts)
        return FinishedParse(outcome)

    def upload(self, parsed: ParsedDocument) -> StoredParseResult:
        """把本地解析结果合并后写入对象存储。"""
        from services.parsing.local_artifacts import store_document

        keys = store_document(parsed, self._reader, _now(), self._attempt_id)
        return StoredParseResult(
            keys.md_key, keys.json_key, keys.manifest_key, keys.part_results
        )

    def collect(
        self,
        document_id: str,
        version: int,
        file_type: str,
        source_key: str,
        parts: Sequence[PartZip],
    ) -> StoredParseResult:
        """收集 MinerU 分段，合并 Markdown 和图片后写入对象存储。"""
        collected = collect_parts(
            document_id,
            version,
            file_type,
            source_key,
            parts,
            _now(),
            self._reader,
            self._attempt_id,
        )
        return StoredParseResult(
            collected.md_key,
            collected.json_key,
            collected.manifest_key,
            collected.part_results,
        )

    def persist(
        self,
        parsed: ParsedDocument,
        stored: StoredParseResult,
    ) -> None:
        """把解析产物的对象 key 写回文档。"""
        document_orm.set_parsed_artifacts(
            parsed.request.document_id,
            stored.md_key,
            stored.json_key,
            _now(),
            expected_version=parsed.request.version,
        )


def _now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)
