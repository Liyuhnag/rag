# -*- coding: utf-8 -*-
"""文档资源接口。"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import Query
from fastapi import UploadFile
from fastapi.responses import Response

from api.deps import get_document_store
from api.deps import get_settings
from api.errors import AppError
from api.uploads import extract_file_type
from api.uploads import normalize_filename
from api.uploads import normalize_operator
from api.uploads import read_limited
from config import Settings
from models.common import Pagination
from models.document import DocumentListResponse
from models.document import DocumentResponse
from models.document import DocumentStatus
from models.document import SortField
from models.document import SortOrder
from services.document_record import DocumentRecord
from services.document_store import DocumentStore

router = APIRouter(prefix="/documents", tags=["documents"])


def _to_response(record: DocumentRecord) -> DocumentResponse:
    return DocumentResponse(
        id=record.id,
        name=record.name,
        type=record.file_type,
        size_bytes=record.size_bytes,
        chunk_count=record.chunk_count,
        status=DocumentStatus(record.status),
        operator=record.operator,
        uploaded_at=record.uploaded_at,
    )


@router.get("", response_model=DocumentListResponse)
def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    sort_by: SortField = Query(
        default=SortField.UPLOADED_AT,
        alias="sortBy",
    ),
    sort_order: SortOrder = Query(
        default=SortOrder.DESC,
        alias="sortOrder",
    ),
    store: DocumentStore = Depends(get_document_store),
) -> DocumentListResponse:
    """分页返回已上传文档。

    查询参数：page、pageSize、sortBy、sortOrder。
    sortBy 可选 uploadedAt、name、sizeBytes。
    """
    records, total_items = store.list_page(
        page,
        page_size,
        sort_by.value,
        sort_order.value,
    )
    if total_items:
        total_pages = (total_items + page_size - 1) // page_size
    else:
        total_pages = 0
    return DocumentListResponse(
        data=[_to_response(record) for record in records],
        pagination=Pagination(
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
        ),
    )


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    operator: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
    store: DocumentStore = Depends(get_document_store),
) -> DocumentResponse:
    """上传一个文档。

    表单字段 file 为文件，operator 可选。
    文件写入对象存储，记录写入数据库，成功返回 201。
    空文件返回 422，超过大小限制返回 413。
    """
    filename = normalize_filename(file.filename)
    file_type = extract_file_type(filename)
    try:
        content = await read_limited(file, settings.max_upload_bytes)
    finally:
        await file.close()
    if len(content) > settings.max_upload_bytes:
        raise AppError(413, "FILE_TOO_LARGE", "文件大小超出限制")
    if not content:
        raise AppError(422, "EMPTY_FILE", "文件内容为空")
    record = store.add(
        name=filename,
        file_type=file_type,
        content=content,
        operator=normalize_operator(operator),
    )
    return _to_response(record)


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    store: DocumentStore = Depends(get_document_store),
) -> DocumentResponse:
    """按文档 ID 返回一条记录。不存在时返回 404。"""
    record = store.get(document_id)
    if record is None:
        raise AppError(404, "NOT_FOUND", "文档不存在")
    return _to_response(record)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: str,
    store: DocumentStore = Depends(get_document_store),
) -> Response:
    """删除文档记录及其对象。成功返回 204，不存在时返回 404。"""
    deleted = store.delete(document_id)
    if deleted is None:
        raise AppError(404, "NOT_FOUND", "文档不存在")
    return Response(status_code=204)
