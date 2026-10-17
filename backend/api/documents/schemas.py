# -*- coding: utf-8 -*-
"""文档接口的查询参数和响应包装。"""

from datetime import datetime
from enum import Enum

from pydantic import Field

from api.common.schemas import APIModel
from api.common.schemas import Pagination
from api.common.schemas import paginate
from model.document import Document
from model.document import DocumentStatus


class SortField(str, Enum):
    UPLOADED_AT = "uploadedAt"
    NAME = "name"
    SIZE_BYTES = "sizeBytes"


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class DocumentListQuery(APIModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort_by: SortField = Field(default=SortField.UPLOADED_AT)
    sort_order: SortOrder = Field(default=SortOrder.DESC)


class DocumentResponse(APIModel):
    id: str
    name: str
    type: str
    size_bytes: int
    chunk_count: int
    status: DocumentStatus
    uploaded_at: datetime
    file_url: str
    previewable: bool
    preview_url: str | None = None


class DocumentListResponse(APIModel):
    data: list[DocumentResponse]
    pagination: Pagination


def to_document_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        name=document.name,
        type=document.file_type,
        size_bytes=document.size_bytes,
        chunk_count=document.chunk_count,
        status=document.status,
        uploaded_at=document.uploaded_at,
        file_url=document.file_url,
        previewable=document.previewable,
        preview_url=document.preview_url,
    )


def to_document_list(
    records: list[Document],
    page: int,
    page_size: int,
    total_items: int,
) -> DocumentListResponse:
    return DocumentListResponse(
        data=[to_document_response(record) for record in records],
        pagination=paginate(page, page_size, total_items),
    )
