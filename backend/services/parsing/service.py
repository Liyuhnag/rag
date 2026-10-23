# -*- coding: utf-8 -*-
"""文档解析服务。

负责三件事：按文件类型解析、把解析产物上传到对象存储、把结果写入数据库。
读取源文件和具体格式处理由解析器完成。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from model.document import orm as document_orm
from model.document.model import Document
from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.collect import CollectedParse
from services.parsing.collect import PartZip
from services.parsing.collect import StoredObject
from services.parsing.collect import collect_document
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

    def __init__(self, reader: ObjectReader | None = None) -> None:
        self._reader = reader or StorageObjectReader()

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
        parser = build_default_registry().get(document.file_type)
        outcome = parser.parse(ParseInput(request=request))
        if isinstance(outcome, ParseSubmission):
            return SubmittedParse(parts=outcome.parts)
        return FinishedParse(outcome)

    def upload(self, parsed: ParsedDocument) -> StoredParseResult:
        """把本地解析结果合并后写入对象存储。"""
        return self._store(collect_document(parsed, _now()))

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
        )
        return self._store(collected)

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
        )

    def _store(self, collected: CollectedParse) -> StoredParseResult:
        """把收集结果写入对象存储。"""
        self._write(collected.objects)
        keys = collected.result
        return StoredParseResult(
            md_key=keys.md_key,
            json_key=keys.json_key,
            manifest_key=keys.manifest_key,
            part_results=keys.part_results,
        )

    def _write(self, objects: Sequence[StoredObject]) -> None:
        """按清单逐个写入。"""
        for item in objects:
            self._reader.write_object(item.key, item.body, item.content_type)


def _now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)
