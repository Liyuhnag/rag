# -*- coding: utf-8 -*-
"""完成上传：合并分片并写入文档、任务。"""

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from uuid import uuid4

from model.document.model import Document
from model.document.model import DocumentStatus
from model.document_task.model import DocumentTask
from model.upload import orm as upload_orm
from model.upload.model import UploadSession
from services.common.exceptions import StateConflict
from services.common.exceptions import StorageError
from services.common.exceptions import UploadNotFound
from services.common.object_storage import client as storage_client
from services.upload.errors import UploadError
from services.upload.upload_shared import file_url
from services.upload.upload_shared import part_size
from services.upload.upload_shared import parts_or_none
from services.upload.upload_shared import task_for


@dataclass
class CompleteResult:
    session: UploadSession
    task: DocumentTask | None
    outcome: str


def complete_upload(upload_id: str) -> CompleteResult:
    """抢占完成、校验分片、合并并落库。"""
    session = upload_orm.get(upload_id)
    if session is None:
        raise UploadError(404, "UPLOAD_NOT_FOUND", "上传会话不存在")
    if session.status != "UPLOADING":
        return _complete_state(upload_id)

    now = datetime.now(timezone.utc)
    if session.expires_at <= now:
        raise UploadError(410, "UPLOAD_EXPIRED", "上传已过期")

    if not upload_orm.claim_completing(upload_id, now):
        return _complete_state(upload_id)

    session = upload_orm.get(upload_id)
    if session is None:
        raise UploadError(404, "UPLOAD_NOT_FOUND", "上传会话不存在")

    parts = parts_or_none(session)
    if parts is None:
        upload_orm.mark_failed(session.id, "分片上传已不存在", now)
        raise UploadError(410, "UPLOAD_EXPIRED", "上传已过期")

    uploaded = {part.part_number: part for part in parts}
    missing = [
        number
        for number in range(1, session.total_parts + 1)
        if number not in uploaded
    ]
    if missing:
        upload_orm.revert_to_uploading(session.id, session.claim_version, now)
        raise UploadError(
            409,
            "UPLOAD_INCOMPLETE",
            "还有分片未上传",
            {"missingParts": missing},
        )

    invalid = [
        number
        for number in range(1, session.total_parts + 1)
        if uploaded[number].size_bytes != part_size(session, number)
    ]
    if invalid:
        upload_orm.revert_to_uploading(session.id, session.claim_version, now)
        raise UploadError(
            409,
            "UPLOAD_INCOMPLETE",
            "分片大小不完整",
            {"invalidParts": invalid},
        )

    try:
        storage_client().complete_multipart_upload(
            session.object_key,
            session.s3_upload_id,
            [
                {
                    "PartNumber": number,
                    "ETag": uploaded[number].etag,
                }
                for number in range(1, session.total_parts + 1)
            ],
        )
    except UploadNotFound as exc:
        upload_orm.mark_failed(session.id, "分片上传已不存在", now)
        raise UploadError(410, "UPLOAD_EXPIRED", "上传已过期") from exc

    document = _completed_document(session, now)
    task = _new_task(session)

    try:
        upload_orm.complete_session(
            session.id,
            session.claim_version,
            document,
            task,
            now,
        )
    except StateConflict as exc:
        raise UploadError(
            409,
            "UPLOAD_COMPLETING",
            "上传会话状态已变化",
        ) from exc

    session.status = "COMPLETED"
    session.completed_at = now
    return CompleteResult(session, task, "completed")


def _complete_state(upload_id: str) -> CompleteResult:
    session = upload_orm.get(upload_id)
    if session is None:
        raise UploadError(404, "UPLOAD_NOT_FOUND", "上传会话不存在")
    if session.status == "COMPLETED":
        return CompleteResult(session, task_for(session), "completed")
    if session.status == "COMPLETING":
        recovered = recover_completing(session)
        if recovered is not None:
            return recovered
        return CompleteResult(session, None, "completing")
    if session.status == "ABORTING":
        raise UploadError(409, "UPLOAD_ABORTING", "上传正在取消")
    if session.status in {"ABORTED", "EXPIRED"}:
        raise UploadError(410, "UPLOAD_ABORTED", "上传已取消")
    if session.status == "FAILED":
        raise UploadError(409, "UPLOAD_FAILED", "上传已失败，请重新上传")
    raise UploadError(409, "UPLOAD_COMPLETING", "上传正在完成")


def recover_completing(session: UploadSession) -> CompleteResult | None:
    """S3 已合并但数据库没写完时，补写文档和任务。"""
    now = datetime.now(timezone.utc)
    if session.updated_at + timedelta(seconds=300) > now:
        return None
    try:
        size = storage_client().head_object(session.object_key)
    except StorageError:
        return None
    if size != session.size_bytes:
        return None
    document = _completed_document(session, now)
    task = _new_task(session)
    try:
        upload_orm.complete_session(
            session.id,
            session.claim_version,
            document,
            task,
            now,
        )
    except StateConflict:
        return None
    session.status = "COMPLETED"
    session.completed_at = now
    return CompleteResult(session, task, "completed")


def _completed_document(session: UploadSession, now: datetime) -> Document:
    return Document(
        id=session.document_id,
        name=session.filename,
        file_type=session.file_type,
        size_bytes=session.size_bytes,
        chunk_count=0,
        status=DocumentStatus.UPLOADED,
        uploaded_at=now,
        updated_at=now,
        object_key=session.object_key,
        file_url=file_url(session.object_key),
        previewable=False,
        preview_url=None,
    )


def _new_task(session: UploadSession) -> DocumentTask:
    return DocumentTask(
        id=uuid4().hex,
        document_id=session.document_id,
        version=1,
        status="PENDING",
        stage="PENDING",
        progress=0,
        step="等待处理",
    )
