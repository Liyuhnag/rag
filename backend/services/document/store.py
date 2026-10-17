# -*- coding: utf-8 -*-
"""文档服务。原始文件进入对象存储，记录进入数据库。"""

from datetime import datetime
from datetime import timezone
from urllib.parse import quote
from uuid import uuid4

from collections.abc import Callable

from config import Settings
from model.common.database import configure as configure_database
from model.document import Document
from model.document import DocumentStatus
from model.document import orm as document_orm
from services.document.object_storage import ObjectStorage
from services.document.object_storage import S3ObjectStorage
from services.exceptions import StorageError


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
        insert_document: Callable[[Document], None],
        get_document: Callable[[str], Document | None],
        delete_document: Callable[[str], Document | None],
        list_documents: Callable[
            [int, int, str, str],
            tuple[list[Document], int],
        ],
        bucket: str = "",
        file_base_url: str = "",
    ) -> None:
        self.objects = objects
        self.insert_document = insert_document
        self.get_document = get_document
        self.delete_document = delete_document
        self.list_documents = list_documents
        self.bucket = bucket
        self.file_base_url = file_base_url

    def add(
        self,
        name: str,
        file_type: str,
        content: bytes,
    ) -> Document:
        document_id = uuid4().hex
        uploaded_at = datetime.now(timezone.utc)
        object_key = f"documents/{document_id}/{name}"
        document = Document(
            id=document_id,
            name=name,
            file_type=file_type,
            size_bytes=len(content),
            chunk_count=0,
            status=DocumentStatus.UPLOADED,
            uploaded_at=uploaded_at,
            updated_at=uploaded_at,
            object_key=object_key,
            file_url=_file_url(self.file_base_url, self.bucket, object_key),
            previewable=False,
            preview_url=None,
        )
        self.objects.put(
            document.object_key,
            content,
            CONTENT_TYPES.get(file_type, "application/octet-stream"),
        )
        try:
            self.insert_document(document)
        except StorageError:
            self.objects.delete(document.object_key)
            raise
        return document

    def get(self, document_id: str) -> Document | None:
        return self.get_document(document_id)

    def delete(self, document_id: str) -> Document | None:
        document = self.delete_document(document_id)
        if document is None:
            return None
        self.objects.delete(document.object_key)
        return document

    def list_page(
        self,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[Document], int]:
        """按页查询。sort_by 使用接口字段名。"""
        return self.list_documents(
            page,
            page_size,
            _sort_column(sort_by),
            sort_order,
        )


def build_document_store(settings: Settings) -> DocumentStore:
    """按配置组装文档服务。"""
    configure_database(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
    )
    return DocumentStore(
        S3ObjectStorage(
            endpoint=settings.s3_endpoint,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            bucket=settings.s3_bucket,
        ),
        document_orm.insert,
        document_orm.get,
        document_orm.delete,
        document_orm.list_page,
        bucket=settings.s3_bucket,
        file_base_url=settings.file_base_url,
    )


def _sort_column(sort_by: str) -> str:
    """接口排序字段对应 documents 表的列。"""
    if sort_by == "name":
        return "name"
    if sort_by == "sizeBytes":
        return "size_bytes"
    return "uploaded_at"


def _file_url(base_url: str, bucket: str, object_key: str) -> str:
    """原始文件的访问地址。预览链接等大小规则确定后再生成。"""
    base = base_url.rstrip("/")
    if not base or not bucket:
        return ""
    path = "/".join(quote(part, safe="") for part in object_key.split("/"))
    return f"{base}/buckets/{bucket}/{path}"
