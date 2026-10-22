# -*- coding: utf-8 -*-
"""父子块切分依赖的端口。"""

from typing import Protocol

from model.document_chunk.model import DocumentChunk
from services.chunking.models import ChunkOptions
from services.parsing.models import ParsedDocument


class ChunkStore(Protocol):
    """替换某个文档版本的切分结果。"""

    def replace_for_version(
        self,
        document_id: str,
        version: int,
        chunks: list[DocumentChunk],
    ) -> None:
        ...


class DocumentChunker(Protocol):
    """把解析结果切成父子块。"""

    def split(
        self,
        parsed: ParsedDocument,
        options: ChunkOptions,
    ) -> list[DocumentChunk]:
        ...
