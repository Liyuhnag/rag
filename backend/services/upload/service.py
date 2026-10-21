# -*- coding: utf-8 -*-
"""上传会话服务：创建、复用和完成态查询。"""

import mimetypes
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from uuid import uuid4

from services.common.exceptions import DuplicateRecord
from services.common.exceptions import FingerprintConflict
from services.common.exceptions import StorageError
from services.common.exceptions import UploadNotFound
from model.document import orm as document_orm
from model.document_task import orm as document_task_orm
from model.document_task.model import DocumentTask
from model.upload import orm as upload_orm
from model.upload.model import UploadSession
from services.common.object_storage import UploadedPart
from services.common.object_storage import client as storage_client


@dataclass
class InitResult:
    session: UploadSession
    uploaded_parts: list[UploadedPart]
    outcome: str
    task: DocumentTask | None = None


def _list_parts(session: UploadSession) -> list[UploadedPart]:
    return storage_client().list_parts(session.object_key, session.s3_upload_id)


def _parts_or_none(session: UploadSession) -> list[UploadedPart] | None:
    """分片上传已从对象存储消失时返回 None。"""
    try:
        return _list_parts(session)
    except UploadNotFound:
        return None


def _require_same_file(
    session: UploadSession,
    filename: str,
    size_bytes: int,
) -> None:
    """指纹相同但文件名或大小不同时，不能续到同一次分片上传。"""
    if session.filename != filename or session.size_bytes != size_bytes:
        raise FingerprintConflict(session.id)


def _match_fingerprint(
    fingerprint: str,
    filename: str,
    size_bytes: int,
    ttl_hours: int,
    now: datetime,
) -> InitResult | None:
    """按状态决定是否直接返回。没有可复用会话时返回 None。

    顺序：未过期的 UPLOADING、COMPLETING、文档仍在的 COMPLETED、ABORTING。
    过期的 UPLOADING 会先取消，然后交给创建流程。
    """
    active = upload_orm.find_active(fingerprint, now)
    if active is not None:
        _require_same_file(active, filename, size_bytes)
        parts = _parts_or_none(active)
        if parts is not None:
            upload_orm.touch(
                active.id,
                now + timedelta(hours=ttl_hours),
                now,
            )
            return InitResult(
                session=active,
                uploaded_parts=parts,
                outcome="reused",
            )
        upload_orm.mark_failed(active.id, "分片上传已不存在", now)

    completing = upload_orm.find_latest(fingerprint, "COMPLETING")
    if completing is not None:
        return InitResult(
            session=completing,
            uploaded_parts=_parts_or_none(completing) or [],
            outcome="completing",
        )

    completed = upload_orm.find_latest(fingerprint, "COMPLETED")
    if completed is not None and document_orm.exists(completed.document_id):
        return InitResult(
            session=completed,
            uploaded_parts=[],
            outcome="completed",
            task=document_task_orm.find(completed.document_id, 1),
        )

    aborting = upload_orm.find_latest(fingerprint, "ABORTING")
    if aborting is not None:
        return InitResult(
            session=aborting,
            uploaded_parts=[],
            outcome="aborting",
        )

    stale = upload_orm.find_latest(fingerprint, "UPLOADING")
    if stale is not None:
        upload_orm.mark_aborting(stale.id, "EXPIRED", now)
        _abort_quietly(stale)
    return None


def _reuse_winner(
    fingerprint: str,
    filename: str,
    size_bytes: int,
    now: datetime,
) -> InitResult:
    """并发创建时，插入失败的一方复用已经写入的那条会话。"""
    existing = upload_orm.find_active(fingerprint, now)
    if existing is None:
        raise StorageError("上传会话创建冲突")
    _require_same_file(existing, filename, size_bytes)
    return InitResult(
        session=existing,
        uploaded_parts=_parts_or_none(existing) or [],
        outcome="reused",
    )


def _open_session(
    *,
    fingerprint: str,
    filename: str,
    file_type: str,
    size_bytes: int,
    chunk_size: int,
    total_parts: int,
    ttl_hours: int,
    now: datetime,
) -> InitResult:
    """发起一次新的分片上传并写入会话。"""
    session = _new_session(
        fingerprint=fingerprint,
        filename=filename,
        file_type=file_type,
        size_bytes=size_bytes,
        chunk_size=chunk_size,
        total_parts=total_parts,
        ttl_hours=ttl_hours,
    )
    try:
        upload_orm.insert(session)
    except DuplicateRecord:
        _abort_quietly(session)
        return _reuse_winner(fingerprint, filename, size_bytes, now)
    except StorageError:
        _abort_quietly(session)
        raise
    return InitResult(
        session=session,
        uploaded_parts=[],
        outcome="created",
    )


def _content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _new_session(
    *,
    fingerprint: str,
    filename: str,
    file_type: str,
    size_bytes: int,
    chunk_size: int,
    total_parts: int,
    ttl_hours: int,
) -> UploadSession:
    now = datetime.now(timezone.utc)
    document_id = uuid4().hex
    object_key = f"documents/{document_id}/source/{filename}"
    upload_id = storage_client().create_multipart_upload(
        object_key,
        _content_type(filename),
    )
    return UploadSession(
        id=uuid4().hex,
        document_id=document_id,
        fingerprint=fingerprint,
        filename=filename,
        file_type=file_type,
        size_bytes=size_bytes,
        chunk_size=chunk_size,
        total_parts=total_parts,
        object_key=object_key,
        s3_upload_id=upload_id,
        status="UPLOADING",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
    )


def _abort_quietly(session: UploadSession) -> None:
    try:
        storage_client().abort_multipart_upload(
            session.object_key,
            session.s3_upload_id,
        )
    except StorageError:
        # 清理失败不覆盖主流程的错误。
        pass


def init_upload(
    *,
    fingerprint: str,
    filename: str,
    file_type: str,
    size_bytes: int,
    chunk_size: int,
    total_parts: int,
    ttl_hours: int,
) -> InitResult:
    """同一指纹先复用已有会话，没有可复用的再创建。"""
    # 检查是否可复用会话
    now = datetime.now(timezone.utc)
    matched = _match_fingerprint(
        fingerprint,
        filename,
        size_bytes,
        ttl_hours,
        now,
    )
    if matched is not None:
        return matched
    # 不可复用会话，创建新会话
    return _open_session(
        fingerprint=fingerprint,
        filename=filename,
        file_type=file_type,
        size_bytes=size_bytes,
        chunk_size=chunk_size,
        total_parts=total_parts,
        ttl_hours=ttl_hours,
        now=now,
    )
