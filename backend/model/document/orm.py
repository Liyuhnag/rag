# -*- coding: utf-8 -*-
"""documents 表的 SQL。服务层只调用这里的函数。"""

from datetime import datetime

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
    """把一行 documents 记录转换为模型。"""
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
        page_count=int(row.get("page_count", 0)),
        parent_chunk_count=int(row.get("parent_chunk_count", 0)),
        parse_version=int(row.get("parse_version", 0)),
        parsed_md_key=(
            None
            if row.get("parsed_md_key") is None
            else str(row["parsed_md_key"])
        ),
        parsed_json_key=(
            None
            if row.get("parsed_json_key") is None
            else str(row["parsed_json_key"])
        ),
        source_sha256=(
            None
            if row.get("source_sha256") is None
            else str(row["source_sha256"])
        ),
        error_message=(
            None
            if row.get("error_message") is None
            else str(row["error_message"])
        ),
    )


def exists(document_id: str) -> bool:
    """按 ID 判断文档是否存在。"""
    rows = execute(
        "SELECT id FROM documents WHERE id = %(id)s LIMIT 1",
        {"id": document_id},
    )
    return bool(rows)


def begin_parse(
    document_id: str,
    parse_version: int,
    now: datetime,
) -> None:
    """开始解析版本，仅允许同版本重试或版本递增。"""
    execute(
        """
        UPDATE documents
        SET status = 'PROCESSING',
            parse_version = %(parse_version)s,
            error_message = NULL,
            updated_at = %(now)s
        WHERE id = %(id)s AND parse_version <= %(parse_version)s
        """,
        {
            "id": document_id,
            "parse_version": parse_version,
            "now": as_utc(now),
        },
    )


def mark_ready(
    document_id: str,
    chunk_count: int,
    parent_chunk_count: int,
    now: datetime,
) -> None:
    """索引完成：写入父子块数量并置 READY。"""
    execute(
        """
        UPDATE documents
        SET status = 'READY',
            chunk_count = %(chunk_count)s,
            parent_chunk_count = %(parent_chunk_count)s,
            error_message = NULL,
            updated_at = %(now)s
        WHERE id = %(id)s
        """,
        {
            "id": document_id,
            "chunk_count": chunk_count,
            "parent_chunk_count": parent_chunk_count,
            "now": as_utc(now),
        },
    )


def mark_failed(
    document_id: str,
    message: str,
    now: datetime,
    *,
    expected_version: int | None = None,
) -> None:
    """记录失败；指定版本时避免旧任务覆盖新版本状态。"""
    sql = """
        UPDATE documents
        SET status = 'FAILED',
            error_message = %(message)s,
            updated_at = %(now)s
        WHERE id = %(id)s
    """
    params: dict[str, object] = {
        "id": document_id,
        "message": message[:1024],
        "now": as_utc(now),
    }
    if expected_version is not None:
        sql += " AND parse_version = %(expected_version)s"
        params["expected_version"] = expected_version
    execute(sql, params)


def set_parsed_artifacts(
    document_id: str,
    parsed_md_key: str | None,
    parsed_json_key: str | None,
    now: datetime,
    *,
    expected_version: int | None = None,
) -> None:
    """记录解析产物；指定版本时仅更新仍处于该版本的文档。"""
    sql = """
        UPDATE documents
        SET parsed_md_key = %(parsed_md_key)s,
            parsed_json_key = %(parsed_json_key)s,
            updated_at = %(now)s
        WHERE id = %(id)s
    """
    params: dict[str, object] = {
        "id": document_id,
        "parsed_md_key": parsed_md_key,
        "parsed_json_key": parsed_json_key,
        "now": as_utc(now),
    }
    if expected_version is not None:
        sql += " AND parse_version = %(expected_version)s"
        params["expected_version"] = expected_version
    execute(sql, params)
