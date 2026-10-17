# -*- coding: utf-8 -*-
"""文档资源接口。"""

from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Query
from fastapi import UploadFile
from fastapi.responses import Response

from api.common.deps import get_settings
from api.documents.errors import not_found
from api.documents.schemas import DocumentListQuery
from api.documents.schemas import DocumentListResponse
from api.documents.schemas import DocumentResponse
from api.documents.schemas import to_document_list
from api.documents.schemas import to_document_response
from api.tasks.schemas import TaskResponse
from api.tasks.schemas import to_task_response
from api.documents.validators import extract_file_type
from api.documents.validators import normalize_filename
from api.documents.validators import read_upload
from config import Settings
from services.document import store as document_store
from task import accept_upload

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=DocumentListResponse)
def list_documents(
    query: Annotated[DocumentListQuery, Query()],
) -> DocumentListResponse:
    """分页返回已上传文档。

    查询参数：page、pageSize、sortBy、sortOrder。
    sortBy 可选 uploadedAt、name、sizeBytes。
    """
    records, total_items = document_store.list_page(
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


@router.post("", response_model=TaskResponse, status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> TaskResponse:
    """接收一个文档并提交异步处理。

    表单字段 file 为文件。
    请求只保存临时文件并返回任务，对象存储和文档记录由任务写入。
    空文件返回 422，超过大小限制返回 413。
    """
    filename = normalize_filename(file.filename)
    file_type = extract_file_type(filename)
    try:
        content = await read_upload(file, settings.max_upload_bytes)
    finally:
        await file.close()
    task = accept_upload(filename, file_type, content)
    return to_task_response(task)


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str) -> DocumentResponse:
    """按文档 ID 返回一条记录。不存在时返回 404。"""
    record = document_store.get(document_id)
    if record is None:
        raise not_found()
    return to_document_response(record)


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: str) -> Response:
    """删除文档记录及其对象。成功返回 204，不存在时返回 404。"""
    deleted = document_store.delete(document_id)
    if deleted is None:
        raise not_found()
    return Response(status_code=204)
