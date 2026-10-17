# -*- coding: utf-8 -*-
"""documents 表的 SQL。服务层只调用这里的函数。"""

from model.common.database import as_aware
from model.common.database import as_utc
from model.common.database import execute
from model.document.model import Document
from model.document.model import DocumentStatus


def insert(document: Document) -> None:
    """写入一条文件记录。"""
    sql = """
        INSERT INTO documents (
            id, name, file_type, size_bytes, chunk_count,
            status, uploaded_at, updated_at, object_key,
            file_url, previewable, preview_url
        ) VALUES (
            %(id)s, %(name)s, %(file_type)s, %(size_bytes)s,
            %(chunk_count)s, %(status)s,
            %(uploaded_at)s, %(updated_at)s, %(object_key)s,
            %(file_url)s, %(previewable)s, %(preview_url)s
        )
    """
    execute(
        sql,
        {
            "id": document.id,
            "name": document.name,
            "file_type": document.file_type,
            "size_bytes": document.size_bytes,
            "chunk_count": document.chunk_count,
            "status": document.status.value,
            "uploaded_at": as_utc(document.uploaded_at),
            "updated_at": as_utc(document.updated_at),
            "object_key": document.object_key,
            "file_url": document.file_url,
            "previewable": int(document.previewable),
            "preview_url": document.preview_url,
        },
    )


def get(document_id: str) -> Document | None:
    """按 ID 读取。不存在时返回 None。"""
    rows = execute(
        "SELECT * FROM documents WHERE id = %(id)s",
        {"id": document_id},
    )
    if not rows:
        return None
    return _from_row(rows[0])


def delete(document_id: str) -> Document | None:
    """删除一条记录，并返回被删的模型。不存在时返回 None。"""
    document = get(document_id)
    if document is None:
        return None
    execute(
        "DELETE FROM documents WHERE id = %(id)s",
        {"id": document_id},
    )
    return document


def list_page(
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
) -> tuple[list[Document], int]:
    """按页返回文件记录和总条数。sort_by 使用表字段名。"""
    if sort_by == "name":
        column = "name"
    elif sort_by == "size_bytes":
        column = "size_bytes"
    else:
        column = "uploaded_at"
    direction = "ASC" if sort_order == "asc" else "DESC"
    count_rows = execute("SELECT COUNT(*) AS total FROM documents", {})
    total = int(count_rows[0]["total"])
    sql = f"""
        SELECT * FROM documents
        ORDER BY {column} {direction}, id ASC
        LIMIT %(limit)s OFFSET %(offset)s
    """
    rows = execute(
        sql,
        {
            "limit": page_size,
            "offset": (page - 1) * page_size,
        },
    )
    return [_from_row(row) for row in rows], total


def _from_row(row: dict[str, object]) -> Document:
    return Document(
        id=str(row["id"]),
        name=str(row["name"]),
        file_type=str(row["file_type"]),
        size_bytes=int(row["size_bytes"]),
        chunk_count=int(row["chunk_count"]),
        status=DocumentStatus(str(row["status"])),
        uploaded_at=as_aware(row["uploaded_at"]),
        updated_at=as_aware(row["updated_at"]),
        object_key=str(row["object_key"]),
        file_url=str(row["file_url"]),
        previewable=bool(row["previewable"]),
        preview_url=(
            None if row["preview_url"] is None else str(row["preview_url"])
        ),
    )
