# -*- coding: utf-8 -*-
"""文档元数据。"""

from datetime import datetime
from datetime import timezone

import pymysql
from pymysql.err import Error as MySQLError

from services.document_record import DocumentRecord
from services.exceptions import StorageError


_SORT_COLUMNS = {
    "uploadedAt": "uploaded_at",
    "name": "name",
    "sizeBytes": "size_bytes",
}


class DocumentRepository:
    """文档记录的读写。"""

    def insert(self, record: DocumentRecord) -> None:
        raise NotImplementedError

    def get(self, document_id: str) -> DocumentRecord | None:
        raise NotImplementedError

    def delete(self, document_id: str) -> DocumentRecord | None:
        raise NotImplementedError

    def list_page(
        self,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[DocumentRecord], int]:
        raise NotImplementedError


class MemoryDocumentRepository(DocumentRepository):
    """测试用的进程内记录。"""

    def __init__(self) -> None:
        self.records: dict[str, DocumentRecord] = {}

    def insert(self, record: DocumentRecord) -> None:
        self.records[record.id] = record

    def get(self, document_id: str) -> DocumentRecord | None:
        return self.records.get(document_id)

    def delete(self, document_id: str) -> DocumentRecord | None:
        return self.records.pop(document_id, None)

    def list_page(
        self,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[DocumentRecord], int]:
        records = list(self.records.values())
        reverse = sort_order != "asc"

        def sort_key(record: DocumentRecord) -> tuple[object, str]:
            if sort_by == "name":
                value: object = record.name
            elif sort_by == "sizeBytes":
                value = record.size_bytes
            else:
                value = record.uploaded_at
            return (value, record.id)

        records.sort(key=sort_key, reverse=reverse)
        start = (page - 1) * page_size
        return records[start : start + page_size], len(records)


class MysqlDocumentRepository(DocumentRepository):
    """MySQL 中的文档记录。"""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self._ensure_schema()

    def insert(self, record: DocumentRecord) -> None:
        sql = """
            INSERT INTO documents (
                id, name, file_type, size_bytes, chunk_count,
                status, operator, uploaded_at, object_key
            ) VALUES (
                %(id)s, %(name)s, %(file_type)s, %(size_bytes)s,
                %(chunk_count)s, %(status)s, %(operator)s,
                %(uploaded_at)s, %(object_key)s
            )
        """
        self._execute(
            sql,
            {
                "id": record.id,
                "name": record.name,
                "file_type": record.file_type,
                "size_bytes": record.size_bytes,
                "chunk_count": record.chunk_count,
                "status": record.status,
                "operator": record.operator,
                "uploaded_at": _as_utc(record.uploaded_at),
                "object_key": record.stored_name,
            },
        )

    def get(self, document_id: str) -> DocumentRecord | None:
        rows = self._query(
            "SELECT * FROM documents WHERE id = %(id)s",
            {"id": document_id},
        )
        if not rows:
            return None
        return _from_row(rows[0])

    def delete(self, document_id: str) -> DocumentRecord | None:
        record = self.get(document_id)
        if record is None:
            return None
        self._execute(
            "DELETE FROM documents WHERE id = %(id)s",
            {"id": document_id},
        )
        return record

    def list_page(
        self,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[DocumentRecord], int]:
        column = _SORT_COLUMNS.get(sort_by, "uploaded_at")
        direction = "ASC" if sort_order == "asc" else "DESC"
        count_rows = self._query("SELECT COUNT(*) AS total FROM documents", {})
        total = int(count_rows[0]["total"])
        sql = f"""
            SELECT * FROM documents
            ORDER BY {column} {direction}, id ASC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        rows = self._query(
            sql,
            {
                "limit": page_size,
                "offset": (page - 1) * page_size,
            },
        )
        return [_from_row(row) for row in rows], total

    def _ensure_schema(self) -> None:
        sql = """
            CREATE TABLE IF NOT EXISTS documents (
                id VARCHAR(32) NOT NULL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                file_type VARCHAR(32) NOT NULL,
                size_bytes BIGINT NOT NULL,
                chunk_count INT NOT NULL,
                status VARCHAR(32) NOT NULL,
                operator VARCHAR(64) NOT NULL,
                uploaded_at DATETIME(6) NOT NULL,
                object_key VARCHAR(768) NOT NULL,
                KEY idx_documents_uploaded_at (uploaded_at),
                KEY idx_documents_name (name),
                KEY idx_documents_size (size_bytes)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        self._execute(sql, {})

    def _connect(self) -> pymysql.Connection:
        try:
            return pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                charset="utf8mb4",
                autocommit=True,
                cursorclass=pymysql.cursors.DictCursor,
            )
        except MySQLError as exc:
            raise StorageError("数据库不可用") from exc

    def _execute(self, sql: str, params: dict[str, object]) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
        except StorageError:
            raise
        except MySQLError as exc:
            raise StorageError("数据库不可用") from exc

    def _query(
        self,
        sql: str,
        params: dict[str, object],
    ) -> list[dict[str, object]]:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    return list(cursor.fetchall())
        except StorageError:
            raise
        except MySQLError as exc:
            raise StorageError("数据库不可用") from exc


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _from_row(row: dict[str, object]) -> DocumentRecord:
    uploaded_at = row["uploaded_at"]
    if isinstance(uploaded_at, datetime) and uploaded_at.tzinfo is None:
        uploaded_at = uploaded_at.replace(tzinfo=timezone.utc)
    return DocumentRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        file_type=str(row["file_type"]),
        size_bytes=int(row["size_bytes"]),
        chunk_count=int(row["chunk_count"]),
        status=str(row["status"]),
        operator=str(row["operator"]),
        uploaded_at=uploaded_at,
        stored_name=str(row["object_key"]),
    )
