# -*- coding: utf-8 -*-
"""文档解析服务的数据结构。"""

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Literal

from model.document_chunk.model import DocumentChunk

BlockKind = Literal["text", "table", "image", "code", "formula"]


@dataclass(frozen=True)
class ParseRequest:
    """一次解析任务需要的源文件信息。"""

    document_id: str
    version: int
    object_key: str
    file_type: str
    filename: str
    size_bytes: int


@dataclass(frozen=True)
class ParseInput:
    """解析器实际读取的本地源文件。"""

    request: ParseRequest
    source_path: Path


@dataclass
class ParsedBlock:
    """解析器输出的最小内容单元。"""

    kind: BlockKind
    text: str
    page: int | None = None
    sheet: str | None = None
    locator: str = ""
    heading_path: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    """一个文档版本的完整解析结果。"""

    request: ParseRequest
    title: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


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
    """解析和切分的完整结果。"""

    parsed: ParsedDocument
    chunks: list[DocumentChunk] = field(default_factory=list)
