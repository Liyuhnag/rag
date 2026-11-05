# -*- coding: utf-8 -*-
"""文档资源接口。"""

from typing import Annotated

from fastapi import APIRouter
from fastapi import Query
from fastapi.responses import Response

from api.documents.errors import not_found
from api.documents.schemas import DocumentListQuery
from api.documents.schemas import DocumentListResponse
from api.documents.schemas import DocumentResponse
from api.documents.schemas import to_document_list
from api.documents.schemas import to_document_response
from services.document import store as document_store

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
