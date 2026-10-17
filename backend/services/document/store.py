# -*- coding: utf-8 -*-
"""文档服务。原始文件进入对象存储，记录进入数据库。"""

from datetime import datetime
from datetime import timezone
from urllib.parse import quote
from uuid import uuid4

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

_storage: dict[str, object] = {
    "objects": None,
    "bucket": "",
    "file_base_url": "",
}


def configure(
    objects: ObjectStorage,
    bucket: str,
    file_base_url: str,
) -> None:
    """保存对象存储客户端和文件链接前缀。"""
    _storage["objects"] = objects
    _storage["bucket"] = bucket
    _storage["file_base_url"] = file_base_url


def connect_storage(settings: Settings) -> None:
    """连接数据库，并按配置接入对象存储。"""
    configure_database(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
    )
    configure(
        S3ObjectStorage(
            endpoint=settings.s3_endpoint,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            bucket=settings.s3_bucket,
        ),
        bucket=settings.s3_bucket,
        file_base_url=settings.file_base_url,
    )


def add(name: str, file_type: str, content: bytes) -> Document:
    """保存文件正文和一条文档记录。写入记录失败时删掉已上传的对象。"""
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
        file_url=_file_url(object_key),
        previewable=False,
        preview_url=None,
    )
    objects = _objects()
    objects.put(
        document.object_key,
        content,
        CONTENT_TYPES.get(file_type, "application/octet-stream"),
    )
    try:
        document_orm.insert(document)
    except StorageError:
        objects.delete(document.object_key)
        raise
    return document


def get(document_id: str) -> Document | None:
    """按 ID 读取一条文档记录。"""
    return document_orm.get(document_id)


def delete(document_id: str) -> Document | None:
    """删除文档记录及其对象。不存在时返回 None。"""
    document = document_orm.delete(document_id)
    if document is None:
        return None
    _objects().delete(document.object_key)
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


def _objects() -> ObjectStorage:
    objects = _storage["objects"]
    if not isinstance(objects, ObjectStorage):
        raise StorageError("对象存储不可用")
    return objects


def _sort_column(sort_by: str) -> str:
    """接口排序字段对应 documents 表的列。"""
    if sort_by == "name":
        return "name"
    if sort_by == "sizeBytes":
        return "size_bytes"
    return "uploaded_at"


def _file_url(object_key: str) -> str:
    """原始文件的访问地址。预览链接等大小规则确定后再生成。"""
    base = str(_storage["file_base_url"]).rstrip("/")
    bucket = str(_storage["bucket"])
    if not base or not bucket:
        return ""
    path = "/".join(quote(part, safe="") for part in object_key.split("/"))
    return f"{base}/buckets/{bucket}/{path}"
