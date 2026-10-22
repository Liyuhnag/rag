# -*- coding: utf-8 -*-
"""文档解析的核心业务服务。

这里只做解析、切分和落库；任务领取、状态流转和异常重试由 task 层编排。
"""

from collections.abc import Mapping

from services.common.exceptions import FeatureNotReady
from services.parsing.models import ChunkOptions
from services.parsing.models import DocumentChunk
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedDocument
from services.parsing.ports import ChunkStore
from services.parsing.ports import DocumentChunker
from services.parsing.ports import DocumentParser
from services.parsing.ports import ObjectReader


class DocumentParsingService:
    """解析、切分和落库的核心业务。"""

    def __init__(
        self,
        *,
        objects: ObjectReader,
        chunks: ChunkStore,
        parsers: Mapping[str, DocumentParser],
        chunker: DocumentChunker,
        chunk_options: ChunkOptions | None = None,
    ) -> None:
        self._objects = objects
        self._chunks = chunks
        self._parsers = parsers
        self._chunker = chunker
        self._chunk_options = chunk_options or ChunkOptions()

    def parse(self, request: ParseRequest) -> ParsedDocument:
        """按文件类型选择解析器并解析源文件。"""
        raise FeatureNotReady("文档解析服务尚未实现：parse")

    def split(
        self,
        parsed: ParsedDocument,
        options: ChunkOptions | None = None,
    ) -> list[DocumentChunk]:
        """把解析结果切成父子块。"""
        raise FeatureNotReady("文档解析服务尚未实现：split")

    def persist(
        self,
        document_id: str,
        version: int,
        chunks: list[DocumentChunk],
    ) -> tuple[int, int]:
        """替换某个版本的切分结果，返回子块数和父块数。"""
        raise FeatureNotReady("文档解析服务尚未实现：persist")
