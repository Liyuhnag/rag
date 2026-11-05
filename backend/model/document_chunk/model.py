# -*- coding: utf-8 -*-
"""文档父子块模型，对应 document_chunks 表的一行。"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ChunkLevel(str, Enum):
    """父子块层级。"""

    PARENT = "PARENT"  # 父块，最终返回给用户的上下文
    CHILD = "CHILD"    # 子块，用于 embedding 和召回


@dataclass
class DocumentChunk:
    """一个父块或子块。"""

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
    created_at: datetime | None = None
