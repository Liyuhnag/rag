# -*- coding: utf-8 -*-
"""父子块切分的数据结构。"""

from dataclasses import dataclass
from dataclasses import field

from model.document_chunk.model import DocumentChunk
from services.parsing.models import ParsedDocument


@dataclass(frozen=True)
class ChunkOptions:
    """父子块切分参数。"""

    max_parent_tokens: int = 1800
    min_parent_tokens: int = 1200
    max_child_tokens: int = 500
    min_child_tokens: int = 300
    overlap_tokens: int = 50


@dataclass
class ParseResult:
    """一份解析结果及其父子块。"""

    parsed: ParsedDocument
    chunks: list[DocumentChunk] = field(default_factory=list)
