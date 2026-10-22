# -*- coding: utf-8 -*-
"""文档解析服务。

负责三件事：按文件类型解析、把解析产物上传到对象存储、把结果写入数据库。
读取源文件和具体格式处理由解析器完成。
"""

from dataclasses import dataclass

from model.document.model import Document
from services.common.exceptions import FeatureNotReady
from services.parsing.models import ParseInput
from services.parsing.models import ParseRequest
from services.parsing.models import ParseSubmission
from services.parsing.models import ParsedDocument
from services.parsing.models import SubmittedPart
from services.parsing.parsers.registry import build_default_registry


@dataclass(frozen=True)
class StoredParseResult:
    """已上传的解析产物位置。"""

    md_key: str
    json_key: str


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
        """把解析产物上传到对象存储。尚未实现。"""
        raise FeatureNotReady("解析产物上传尚未实现")

    def persist(
        self,
        parsed: ParsedDocument,
        stored: StoredParseResult,
    ) -> None:
        """把解析结果写入数据库。尚未实现。"""
        raise FeatureNotReady("解析结果落库尚未实现")
