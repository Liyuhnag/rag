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
from services.common.object_storage import UploadedPart
from services.common.object_storage import client as storage_client
from services.upload.errors import UploadError
from services.upload.errors import upload_aborted
from services.upload.errors import upload_aborting
from services.upload.errors import upload_completing
from services.upload.errors import upload_expired
from services.upload.errors import upload_failed
from services.upload.errors import upload_incomplete
from services.upload.shared import abort_quietly
from services.upload.shared import file_url
from services.upload.shared import part_size
from services.upload.shared import parts_or_none
from services.upload.shared import require_session
from services.upload.shared import task_for
from services.upload.jobs.dispatch import dispatch_task


@dataclass
class CompleteResult:
    session: UploadSession
    task: DocumentTask | None
    outcome: str


def complete_upload(upload_id: str) -> CompleteResult:
    """抢占完成、校验分片、合并并落库。"""
    session = require_session(upload_id)
    if session.status != "UPLOADING":
        return _complete_state(upload_id)

    now = datetime.now(timezone.utc)
    if session.expires_at <= now:
        raise upload_expired()
    if not upload_orm.claim_completing(upload_id, now):
        return _complete_state(upload_id)

    session = require_session(upload_id)
    uploaded = _require_complete_parts(session, now)
    _merge_parts(session, uploaded, now)
    return _store_completed(session, now)


def _require_complete_parts(
    session: UploadSession,
    now: datetime,
) -> dict[int, UploadedPart]:
    """分片必须齐全，且每片大小与约定一致。"""
    parts = parts_or_none(session)
    if parts is None:
        upload_orm.mark_failed(session.id, "分片上传已不存在", now)
        raise upload_expired()

    uploaded = {part.part_number: part for part in parts}
    numbers = range(1, session.total_parts + 1)
    missing = [number for number in numbers if number not in uploaded]
    if missing:
        _reject_incomplete(
            session,
            now,
            "还有分片未上传",
            {"missingParts": missing},
        )
    invalid = [
        number
        for number in numbers
        if uploaded[number].size_bytes != part_size(session, number)
    ]
    if invalid:
        _reject_incomplete(
            session,
            now,
            "分片大小不完整",
            {"invalidParts": invalid},
        )
    return uploaded


def _reject_incomplete(
    session: UploadSession,
    now: datetime,
    message: str,
    details: dict[str, list[int]],
) -> None:
    upload_orm.revert_to_uploading(session.id, session.claim_version, now)
    raise upload_incomplete(message, details)


def _merge_parts(
    session: UploadSession,
    uploaded: dict[int, UploadedPart],
    now: datetime,
) -> None:
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
        raise upload_expired() from exc


def _store_completed(session: UploadSession, now: datetime) -> CompleteResult:
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
        raise upload_completing("上传会话状态已变化") from exc
    session.status = "COMPLETED"
    session.completed_at = now
    dispatch_task(task.id)
    return CompleteResult(session, task, "completed")


def _complete_state(upload_id: str) -> CompleteResult:
    session = require_session(upload_id)
    if session.status == "COMPLETED":
        return CompleteResult(session, task_for(session), "completed")
    if session.status == "COMPLETING":
        recovered = recover_completing(session)
        if recovered is not None:
            return recovered
        return CompleteResult(session, None, "completing")
    if session.status == "ABORTING":
        raise upload_aborting()
    if session.status in {"ABORTED", "EXPIRED"}:
        raise upload_aborted()
    if session.status == "FAILED":
        raise upload_failed()
    raise upload_completing()


def recover_completing(
    session: UploadSession,
    lease_seconds: int = 300,
) -> CompleteResult | None:
    """恢复超时的 COMPLETING 会话。"""
    now = datetime.now(timezone.utc)
    if session.updated_at + timedelta(seconds=lease_seconds) > now:
        return None

    try:
        size = storage_client().head_object(session.object_key)
    except StorageError:
        return None
    if size is not None:
        if size != session.size_bytes:
            upload_orm.mark_failed(session.id, "对象大小不一致", now)
            return None
        try:
            return _store_completed(session, now)
        except UploadError as exc:
            if exc.status_code == 409:
                return None
            raise

    parts = parts_or_none(session)
    if parts is None:
        upload_orm.mark_failed(session.id, "分片上传已不存在", now)
        return None

    uploaded = {part.part_number: part for part in parts}
    numbers = range(1, session.total_parts + 1)
    valid = (
        all(number in uploaded for number in numbers)
        and all(
            uploaded[number].size_bytes == part_size(session, number)
            for number in numbers
        )
    )
    if not valid:
        _revert_or_expire(session, now)
        return None

    try:
        storage_client().complete_multipart_upload(
            session.object_key,
            session.s3_upload_id,
            [
                {
                    "PartNumber": number,
                    "ETag": uploaded[number].etag,
                }
                for number in numbers
            ],
        )
    except UploadNotFound:
        upload_orm.mark_failed(session.id, "分片上传已不存在", now)
        return None
    except StorageError:
        return None

    try:
        return _store_completed(session, now)
    except UploadError as exc:
        if exc.status_code == 409:
            return None
        raise


def _revert_or_expire(session: UploadSession, now: datetime) -> None:
    if session.expires_at <= now:
        if not upload_orm.mark_aborting_completing(
            session.id,
            "EXPIRED",
            now,
        ):
            return
        abort_quietly(session)
        upload_orm.finish_abort(session.id, "EXPIRED", now)
        return
    upload_orm.revert_to_uploading(session.id, session.claim_version, now)


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
