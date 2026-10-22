# -*- coding: utf-8 -*-
"""文档解析服务的数据结构。"""

from dataclasses import dataclass
from dataclasses import field
from typing import Literal

BlockKind = Literal["text", "table", "image", "code", "formula"]
ChunkLevel = Literal["PARENT", "CHILD"]


@dataclass(frozen=True)
class ParseRequest:
    """一次解析任务需要的源文件信息。"""

    document_id: str
    version: int
    object_key: str
    file_type: str
    filename: str
    size_bytes: int


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
class DocumentChunk:
    """写入 document_chunks 的父块或子块。"""

    id: str
    document_id: str
    version: int
    chunk_level: ChunkLevel
    parent_id: str | None
    parent_index: int | None
    child_order: int | None
    chunk_index: int
    child_count: int
    file_type: str
    page_start: int | None
    page_end: int | None
    locator: str
    heading_path: str
    content: str
    token_count: int
    embedding_model: str | None = None


@dataclass
class ParseResult:
    """解析和切分的完整结果。"""

    parsed: ParsedDocument
    chunks: list[DocumentChunk] = field(default_factory=list)
