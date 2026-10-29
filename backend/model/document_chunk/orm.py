# -*- coding: utf-8 -*-
"""document_chunks 表的 SQL。"""

from collections.abc import Iterable
from contextlib import contextmanager
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
        parent_id=(None if row["parent_id"] is None else str(row["parent_id"])),
        parent_index=(
            None if row["parent_index"] is None else int(row["parent_index"])
        ),
        child_order=(None if row["child_order"] is None else int(row["child_order"])),
        chunk_index=int(row["chunk_index"]),
        child_count=int(row["child_count"]),
        file_type=str(row["file_type"]),
        page_start=(None if row["page_start"] is None else int(row["page_start"])),
        page_end=(None if row["page_end"] is None else int(row["page_end"])),
        locator=str(row["locator"]),
        heading_path=str(row["heading_path"]),
        content=str(row["content"]),
        token_count=int(row["token_count"]),
        embedding_model=(
            None if row["embedding_model"] is None else str(row["embedding_model"])
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
        WHERE {" AND ".join(conditions)}
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
        WHERE {" AND ".join(conditions)}
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


def replace_current_version(
    document_id: str,
    version: int,
    chunks: Iterable[DocumentChunk],
) -> bool:
    """持有文档版本锁，分批写块与计数；异常整体回滚。"""
    now = datetime.now(timezone.utc)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT parse_version FROM documents WHERE id = %(id)s FOR UPDATE",
                {"id": document_id},
            )
            row = cursor.fetchone()
            if row is None or int(row["parse_version"]) != version:
                return False
            cursor.execute(
                "DELETE FROM document_chunks WHERE document_id = %(id)s "
                "AND version = %(version)s",
                {"id": document_id, "version": version},
            )
            parents = 0
            children = 0
            batch = []
            iterator = iter(chunks)
            try:
                for chunk in iterator:
                    if chunk.document_id != document_id or chunk.version != version:
                        raise ValueError("切分块的文档或版本不匹配")
                    parents += chunk.chunk_level == ChunkLevel.PARENT
                    children += chunk.chunk_level == ChunkLevel.CHILD
                    batch.append(_insert_params(chunk, now))
                    if len(batch) == 200:
                        _insert_batch(cursor, batch)
                        batch = []
                if batch:
                    _insert_batch(cursor, batch)
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
            cursor.execute(
                "UPDATE documents SET parent_chunk_count = %(parents)s, "
                "chunk_count = %(children)s, updated_at = %(now)s "
                "WHERE id = %(id)s AND parse_version = %(version)s",
                {
                    "parents": parents,
                    "children": children,
                    "now": as_utc(now),
                    "id": document_id,
                    "version": version,
                },
            )
    return True


def _insert_batch(cursor, batch):
    cursor.executemany(
        """
        INSERT INTO document_chunks (
            id, document_id, version, chunk_level, parent_id, parent_index,
            child_order, chunk_index, child_count, file_type, page_start,
            page_end, locator, heading_path, content, token_count,
            embedding_model, created_at
        ) VALUES (
            %(id)s, %(document_id)s, %(version)s, %(chunk_level)s,
            %(parent_id)s, %(parent_index)s, %(child_order)s, %(chunk_index)s,
            %(child_count)s, %(file_type)s, %(page_start)s, %(page_end)s,
            %(locator)s, %(heading_path)s, %(content)s, %(token_count)s,
            %(embedding_model)s, %(created_at)s
        )
        """,
        batch,
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


@contextmanager
def index_current_version(document_id: str, version: int):
    """版本锁覆盖外部写入，阻止重解析和同文档并发索引交错。"""
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT parse_version, chunk_count FROM documents WHERE id = %(id)s FOR UPDATE",
                {"id": document_id},
            )
            row = cursor.fetchone()
            if row is None or int(row["parse_version"]) != version:
                yield None
                return
            yield IndexingSession(cursor, document_id, version, int(row["chunk_count"]))


class IndexingSession:
    """持有当前版本行锁的有界读取与状态提交。"""

    def __init__(self, cursor, document_id: str, version: int, expected_count: int):
        self.cursor = cursor
        self.params = {"id": document_id, "version": version}
        self.expected_count = expected_count

    def iter_children(self, batch_size: int):
        after = -1
        while True:
            self.cursor.execute(
                "SELECT * FROM document_chunks WHERE document_id = %(id)s "
                "AND version = %(version)s AND chunk_level = 'CHILD' "
                "AND chunk_index > %(after)s ORDER BY chunk_index LIMIT %(limit)s",
                dict(self.params, after=after, limit=batch_size),
            )
            rows = self.cursor.fetchall()
            if not rows:
                return
            yield [_from_row(row) for row in rows]
            after = int(rows[-1]["chunk_index"])

    def finish(self, embedding_model: str, count: int) -> None:
        if count != self.expected_count:
            raise RuntimeError("实际索引子块数量与文档计数不一致")
        self.cursor.execute(
            "UPDATE document_chunks SET embedding_model = %(model)s "
            "WHERE document_id = %(id)s AND version = %(version)s AND chunk_level = 'CHILD'",
            dict(self.params, model=embedding_model),
        )
        self.cursor.execute(
            "DELETE FROM document_chunks WHERE document_id = %(id)s AND version < %(version)s",
            self.params,
        )
        self.cursor.execute(
            "UPDATE documents SET status = 'READY', error_message = NULL, updated_at = %(now)s "
            "WHERE id = %(id)s AND parse_version = %(version)s",
            dict(self.params, now=as_utc(datetime.now(timezone.utc))),
        )
