# -*- coding: utf-8 -*-
"""文件模型，对应 documents 表的一行。"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DocumentStatus(str, Enum):
    """文件处理状态。"""

    UPLOADED = "UPLOADED"      # 文件已上传，等待解析
    PROCESSING = "PROCESSING"  # 正在解析或建立索引
    READY = "READY"            # 解析和索引完成，可检索
    FAILED = "FAILED"          # 解析或索引失败


@dataclass
class Document:
    """已上传文件的元数据。文件内容在对象存储。"""

    id: str
    name: str
    file_type: str
    size_bytes: int
    chunk_count: int
    status: DocumentStatus
    uploaded_at: datetime
    updated_at: datetime
    object_key: str
    file_url: str
    previewable: bool
    preview_url: str | None
    page_count: int = 0
    parent_chunk_count: int = 0
    parse_version: int = 0
    parsed_md_key: str | None = None
    parsed_json_key: str | None = None
    source_sha256: str | None = None
    error_message: str | None = None
