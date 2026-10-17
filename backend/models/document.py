# -*- coding: utf-8 -*-
"""文档资源的请求与响应模型。"""

from datetime import datetime
from enum import Enum

from models.common import APIModel
from models.common import Pagination


class DocumentStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class SortField(str, Enum):
    UPLOADED_AT = "uploadedAt"
    NAME = "name"
    SIZE_BYTES = "sizeBytes"


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class DocumentResponse(APIModel):
    id: str
    name: str
    type: str
    size_bytes: int
    chunk_count: int
    status: DocumentStatus
    operator: str
    uploaded_at: datetime


class DocumentListResponse(APIModel):
    data: list[DocumentResponse]
    pagination: Pagination
