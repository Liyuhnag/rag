# -*- coding: utf-8 -*-
"""文档服务。原始文件进入对象存储，记录进入数据库。"""

from datetime import datetime
from datetime import timezone
from uuid import uuid4

from config import Settings
from services.document_record import DocumentRecord
from services.document_repository import DocumentRepository
from services.document_repository import MemoryDocumentRepository
from services.document_repository import MysqlDocumentRepository
from services.exceptions import StorageError
from services.object_storage import MemoryObjectStorage
from services.object_storage import ObjectStorage
from services.object_storage import S3ObjectStorage


CONTENT_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "doc": "application/msword",
    "docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
    "xls": "application/vnd.ms-excel",
    "xlsx": (
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"
    ),
}


class DocumentStore:
    """保存上传文件并维护文档记录。"""

    def __init__(
        self,
        objects: ObjectStorage,
        records: DocumentRepository,
    ) -> None:
        self.objects = objects
        self.records = records

    def add(
        self,
        name: str,
        file_type: str,
        content: bytes,
        operator: str,
    ) -> DocumentRecord:
        document_id = uuid4().hex
        record = DocumentRecord(
            id=document_id,
            name=name,
            file_type=file_type,
            size_bytes=len(content),
            chunk_count=0,
            status="UPLOADED",
            operator=operator,
            uploaded_at=datetime.now(timezone.utc),
            stored_name=f"documents/{document_id}/{name}",
        )
        self.objects.put(
            record.stored_name,
            content,
            CONTENT_TYPES.get(file_type, "application/octet-stream"),
        )
        try:
            self.records.insert(record)
        except StorageError:
            self.objects.delete(record.stored_name)
            raise
        return record

    def get(self, document_id: str) -> DocumentRecord | None:
        return self.records.get(document_id)

    def delete(self, document_id: str) -> DocumentRecord | None:
        record = self.records.delete(document_id)
        if record is None:
            return None
        self.objects.delete(record.stored_name)
        return record

    def list_page(
        self,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[DocumentRecord], int]:
        return self.records.list_page(page, page_size, sort_by, sort_order)


def build_document_store(settings: Settings) -> DocumentStore:
    """按配置组装文档服务。测试默认使用内存实现。"""
    if settings.storage_backend == "memory":
        return DocumentStore(
            MemoryObjectStorage(),
            MemoryDocumentRepository(),
        )
    return DocumentStore(
        S3ObjectStorage(
            endpoint=settings.s3_endpoint,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            bucket=settings.s3_bucket,
        ),
        MysqlDocumentRepository(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database,
        ),
    )
