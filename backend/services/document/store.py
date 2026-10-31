# -*- coding: utf-8 -*-
"""文档查询和删除。文件内容在对象存储，记录在数据库。"""

from model.document import Document
from model.document import orm as document_orm
from services.common.object_storage import client


def get(document_id: str) -> Document | None:
    """按 ID 读取一条文档记录。"""
    return document_orm.get(document_id)


def delete(document_id: str) -> Document | None:
    """删除文档记录及其对象。不存在时返回 None。"""
    document = document_orm.delete(document_id)
    if document is None:
        return None
    client().delete(document.object_key)
    return document


def list_page(
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
) -> tuple[list[Document], int]:
    """按页查询。sort_by 使用接口字段名。"""
    return document_orm.list_page(
        page,
        page_size,
        _sort_column(sort_by),
        sort_order,
    )


def _sort_column(sort_by: str) -> str:
    """接口排序字段对应 documents 表的列。"""
    if sort_by == "name":
        return "name"
    if sort_by == "sizeBytes":
        return "size_bytes"
    return "uploaded_at"
