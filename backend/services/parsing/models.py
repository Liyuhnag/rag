# -*- coding: utf-8 -*-
"""文档解析服务的数据结构。"""

from dataclasses import dataclass
from dataclasses import field
from typing import Literal

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
    """交给解析器的请求。文件由解析器从对象存储读取。"""

    request: ParseRequest


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
class SubmittedPart:
    """已提交给 MinerU 的一段。整文件提交时页码尚未测量，page_end 为 0。"""

    file_id: str
    job_id: str
    part_index: int
    page_start: int
    page_end: int
    source_key: str


@dataclass(frozen=True)
class ParseSubmission:
    """解析器已把文件提交给外部服务，还没有解析结果。"""

    parts: tuple[SubmittedPart, ...]
