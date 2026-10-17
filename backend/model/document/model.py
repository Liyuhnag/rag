# -*- coding: utf-8 -*-
"""文件模型，对应 documents 表的一行。"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DocumentStatus(str, Enum):
    """文件处理状态。"""

    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


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
