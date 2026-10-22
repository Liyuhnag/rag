# -*- coding: utf-8 -*-
"""document_chunks 表的 SQL。"""

from datetime import datetime
from datetime import timezone

from model.common.database import as_aware
from model.common.database import as_utc
from model.common.database import execute
from model.common.database import execute_count
from model.common.database import transaction
from model.document_chunk.model import ChunkLevel
from model.document_chunk.model import DocumentChunk


def _from_row(row: dict[str, object]) -> DocumentChunk:
    """把一行 document_chunks 记录转换为模型。"""
    return DocumentChunk(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        version=int(row["version"]),
        chunk_level=ChunkLevel(str(row["chunk_level"])),
        parent_id=(
            None if row["parent_id"] is None else str(row["parent_id"])
        ),
        parent_index=(
            None if row["parent_index"] is None else int(row["parent_index"])
        ),
        child_order=(
            None if row["child_order"] is None else int(row["child_order"])
        ),
        chunk_index=int(row["chunk_index"]),
        child_count=int(row["child_count"]),
        file_type=str(row["file_type"]),
        page_start=(
            None if row["page_start"] is None else int(row["page_start"])
        ),
        page_end=(
            None if row["page_end"] is None else int(row["page_end"])
        ),
        locator=str(row["locator"]),
        heading_path=str(row["heading_path"]),
        content=str(row["content"]),
        token_count=int(row["token_count"]),
        embedding_model=(
            None
            if row["embedding_model"] is None
            else str(row["embedding_model"])
        ),
        created_at=as_aware(row["created_at"]),
    )


def _insert_params(chunk: DocumentChunk, now: datetime) -> dict[str, object]:
    """构造单条 chunk 的 INSERT 参数。"""
    return {
        "id": chunk.id,
        "document_id": chunk.document_id,
        "version": chunk.version,
        "chunk_level": chunk.chunk_level.value,
        "parent_id": chunk.parent_id,
        "parent_index": chunk.parent_index,
        "child_order": chunk.child_order,
        "chunk_index": chunk.chunk_index,
        "child_count": chunk.child_count,
        "file_type": chunk.file_type,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "locator": chunk.locator,
        "heading_path": chunk.heading_path,
        "content": chunk.content,
        "token_count": chunk.token_count,
        "embedding_model": chunk.embedding_model,
        "created_at": as_utc(chunk.created_at or now),
    }


def get(chunk_id: str) -> DocumentChunk | None:
    """按 chunk ID 查询一个父块或子块；不存在返回 None。"""
    rows = execute(
        "SELECT * FROM document_chunks WHERE id = %(id)s LIMIT 1",
        {"id": chunk_id},
    )
    return None if not rows else _from_row(rows[0])


def list_by_version(
    document_id: str,
    version: int,
    chunk_level: ChunkLevel | None = None,
) -> list[DocumentChunk]:
    """按文档和版本查询块，按父块优先、块内序号升序返回。"""
    conditions = [
        "document_id = %(document_id)s",
        "version = %(version)s",
    ]
    params: dict[str, object] = {
        "document_id": document_id,
        "version": version,
    }
    if chunk_level is not None:
        conditions.append("chunk_level = %(chunk_level)s")
        params["chunk_level"] = chunk_level.value
    rows = execute(
        f"""
        SELECT * FROM document_chunks
        WHERE {' AND '.join(conditions)}
        ORDER BY chunk_level DESC, chunk_index ASC
        """,
        params,
    )
    return [_from_row(row) for row in rows]


def count_by_version(
    document_id: str,
    version: int,
    chunk_level: ChunkLevel | None = None,
) -> int:
    """统计某个文档版本的块数量，可按 PARENT/CHILD 过滤。"""
    conditions = [
        "document_id = %(document_id)s",
        "version = %(version)s",
    ]
    params: dict[str, object] = {
        "document_id": document_id,
        "version": version,
    }
    if chunk_level is not None:
        conditions.append("chunk_level = %(chunk_level)s")
        params["chunk_level"] = chunk_level.value
    rows = execute(
        f"""
        SELECT COUNT(*) AS total
        FROM document_chunks
        WHERE {' AND '.join(conditions)}
        """,
        params,
    )
    return int(rows[0]["total"])


def replace_for_version(
    document_id: str,
    version: int,
    chunks: list[DocumentChunk],
) -> None:
    """在一个事务内删除旧块并写入新块，保证版本切换原子性。"""
    now = datetime.now(timezone.utc)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM document_chunks
                WHERE document_id = %(document_id)s
                  AND version = %(version)s
                """,
                {"document_id": document_id, "version": version},
            )
            if chunks:
                cursor.executemany(
                    """
                    INSERT INTO document_chunks (
                        id, document_id, version, chunk_level,
                        parent_id, parent_index, child_order, chunk_index,
                        child_count, file_type, page_start, page_end,
                        locator, heading_path, content, token_count,
                        embedding_model, created_at
                    ) VALUES (
                        %(id)s, %(document_id)s, %(version)s,
                        %(chunk_level)s, %(parent_id)s, %(parent_index)s,
                        %(child_order)s, %(chunk_index)s, %(child_count)s,
                        %(file_type)s, %(page_start)s, %(page_end)s,
                        %(locator)s, %(heading_path)s, %(content)s,
                        %(token_count)s, %(embedding_model)s, %(created_at)s
                    )
                    """,
                    [_insert_params(chunk, now) for chunk in chunks],
                )


def delete_for_version(document_id: str, version: int) -> int:
    """删除某个文档版本的全部块，返回删除行数。"""
    return execute_count(
        """
        DELETE FROM document_chunks
        WHERE document_id = %(document_id)s
          AND version = %(version)s
        """,
        {"document_id": document_id, "version": version},
    )


def delete_by_document(document_id: str) -> int:
    """删除一个文档的所有版本块，返回删除行数。"""
    return execute_count(
        "DELETE FROM document_chunks WHERE document_id = %(document_id)s",
        {"document_id": document_id},
    )
