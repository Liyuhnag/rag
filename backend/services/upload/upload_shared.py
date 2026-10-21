# -*- coding: utf-8 -*-
"""上传流程共用的辅助函数。"""

from urllib.parse import quote

from model.document_task import orm as document_task_orm
from model.document_task.model import DocumentTask
from model.upload.model import UploadSession
from services.common.exceptions import StorageError
from services.common.exceptions import UploadNotFound
from services.common.object_storage import UploadedPart
from services.common.object_storage import bucket as storage_bucket
from services.common.object_storage import client as storage_client
from services.common.object_storage import file_base_url as storage_file_base_url


def list_parts(session: UploadSession) -> list[UploadedPart]:
    return storage_client().list_parts(session.object_key, session.s3_upload_id)


def parts_or_none(session: UploadSession) -> list[UploadedPart] | None:
    """分片上传已从对象存储消失时返回 None。"""
    try:
        return list_parts(session)
    except UploadNotFound:
        return None


def abort_quietly(session: UploadSession) -> None:
    try:
        storage_client().abort_multipart_upload(
            session.object_key,
            session.s3_upload_id,
        )
    except StorageError:
        # 清理失败不覆盖主流程的错误。
        pass


def file_url(object_key: str) -> str:
    base = storage_file_base_url().rstrip("/")
    name = storage_bucket()
    if not base or not name:
        return ""
    path = "/".join(quote(part, safe="") for part in object_key.split("/"))
    return f"{base}/buckets/{name}/{path}"


def part_size(session: UploadSession, part_number: int) -> int:
    if part_number < session.total_parts:
        return session.chunk_size
    return session.size_bytes - (session.total_parts - 1) * session.chunk_size


def task_for(session: UploadSession) -> DocumentTask | None:
    return document_task_orm.find(session.document_id, 1)
