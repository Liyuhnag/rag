# -*- coding: utf-8 -*-
"""文档资源接口。"""

from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Query
from fastapi import UploadFile
from fastapi.responses import Response

from api.common.deps import get_document_store
from api.common.deps import get_settings
from api.documents.errors import not_found
from api.documents.schemas import DocumentListQuery
from api.documents.schemas import DocumentListResponse
from api.documents.schemas import DocumentResponse
from api.documents.schemas import to_document_list
from api.documents.schemas import to_document_response
from api.documents.validators import extract_file_type
from api.documents.validators import normalize_filename
from api.documents.validators import read_upload
from config import Settings
from services.document import DocumentStore

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=DocumentListResponse)
def list_documents(
    query: Annotated[DocumentListQuery, Query()],
    store: DocumentStore = Depends(get_document_store),
) -> DocumentListResponse:
    """分页返回已上传文档。

    查询参数：page、pageSize、sortBy、sortOrder。
    sortBy 可选 uploadedAt、name、sizeBytes。
    """
    records, total_items = store.list_page(
        query.page,
        query.page_size,
        query.sort_by.value,
        query.sort_order.value,
    )
    return to_document_list(
        records,
        query.page,
        query.page_size,
        total_items,
    )


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    store: DocumentStore = Depends(get_document_store),
) -> DocumentResponse:
    """上传一个文档。

    表单字段 file 为文件。
    文件写入对象存储，记录写入数据库，成功返回 201。
    空文件返回 422，超过大小限制返回 413。
    """
    filename = normalize_filename(file.filename)
    file_type = extract_file_type(filename)
    try:
        content = await read_upload(file, settings.max_upload_bytes)
    finally:
        await file.close()
    record = store.add(
        name=filename,
        file_type=file_type,
        content=content,
    )
    return to_document_response(record)


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    store: DocumentStore = Depends(get_document_store),
) -> DocumentResponse:
    """按文档 ID 返回一条记录。不存在时返回 404。"""
    record = store.get(document_id)
    if record is None:
        raise not_found()
    return to_document_response(record)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: str,
    store: DocumentStore = Depends(get_document_store),
) -> Response:
    """删除文档记录及其对象。成功返回 204，不存在时返回 404。"""
    deleted = store.delete(document_id)
    if deleted is None:
        raise not_found()
    return Response(status_code=204)
