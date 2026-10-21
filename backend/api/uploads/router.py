# -*- coding: utf-8 -*-
"""大文件上传接口。

当前为前后端联调版本：接口契约和状态流转已就位，分片暂存在进程内，
不访问对象存储和 MySQL。后续替换服务层实现，路由和响应保持不变。
"""

import re
from datetime import datetime
from datetime import timezone

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from fastapi import Response

from api.common.deps import get_settings
from api.common.errors import AppError
from api.documents.validators import extract_file_type
from api.documents.validators import normalize_filename
from api.uploads import errors
from api.uploads.schemas import UploadCompleteResponse
from api.uploads.schemas import UploadedPart
from api.uploads.schemas import UploadInitRequest
from api.uploads.schemas import UploadInitResponse
from api.uploads.schemas import UploadPartResponse
from api.uploads.schemas import UploadStatusResponse
from config import Settings
from services.upload import store

router = APIRouter(prefix="/uploads", tags=["uploads"])

_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EMPTY_PART_STATUSES = {"COMPLETED", "ABORTING", "ABORTED", "EXPIRED", "FAILED"}
_MAX_TOTAL_PARTS = 10000
_MAX_OBJECT_KEY_BYTES = 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_filename(filename: str) -> str:
    try:
        return normalize_filename(filename)
    except AppError as exc:
        raise errors.invalid_filename() from exc


def _file_type(filename: str) -> str:
    try:
        return extract_file_type(filename)
    except AppError as exc:
        raise errors.unsupported_file_type() from exc


def _uploaded_parts(session: store.UploadSession) -> list[UploadedPart]:
    if session.status in _EMPTY_PART_STATUSES:
        return []
    return [
        UploadedPart(part_number=number, size_bytes=size)
        for number, size in sorted(session.uploaded_parts.items())
    ]


def _task_status(session: store.UploadSession) -> tuple[str | None, str | None]:
    task = store.get_task(session.task_id)
    if task is None:
        return None, None
    return task.id, task.status


def _init_response(session: store.UploadSession) -> UploadInitResponse:
    task_id, task_status = _task_status(session)
    return UploadInitResponse(
        upload_id=session.id,
        document_id=session.document_id,
        status=session.status,
        chunk_size=session.chunk_size,
        total_parts=session.total_parts,
        uploaded_parts=_uploaded_parts(session),
        expires_at=session.expires_at,
        task_id=task_id,
        task_status=task_status,
    )


def _status_response(session: store.UploadSession) -> UploadStatusResponse:
    task_id, task_status = _task_status(session)
    return UploadStatusResponse(
        upload_id=session.id,
        document_id=session.document_id,
        filename=session.filename,
        size_bytes=session.size_bytes,
        chunk_size=session.chunk_size,
        total_parts=session.total_parts,
        status=session.status,
        uploaded_parts=_uploaded_parts(session),
        expires_at=session.expires_at,
        error_message=session.error_message,
        task_id=task_id,
        task_status=task_status,
    )


def _expected_part_size(session: store.UploadSession, part_number: int) -> int:
    if part_number < session.total_parts:
        return session.chunk_size
    return session.size_bytes - (session.total_parts - 1) * session.chunk_size


@router.post("/init", response_model=UploadInitResponse)
def init_upload(
    payload: UploadInitRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> UploadInitResponse:
    """创建或复用上传会话。"""
    filename = _normalize_filename(payload.filename)
    file_type = _file_type(filename)

    if _FINGERPRINT_PATTERN.fullmatch(payload.fingerprint) is None:
        raise errors.invalid_fingerprint()
    if payload.size_bytes <= 0:
        raise errors.invalid_size()
    if payload.size_bytes > settings.max_document_bytes:
        raise errors.file_too_large()

    chunk_size = settings.upload_chunk_bytes
    total_parts = (payload.size_bytes + chunk_size - 1) // chunk_size
    if total_parts > _MAX_TOTAL_PARTS:
        raise errors.invalid_total_parts()

    now = _now()
    active = store.find_active(payload.fingerprint, now)
    if active is not None:
        if active.filename != filename or active.size_bytes != payload.size_bytes:
            raise errors.fingerprint_conflict(active.id)
        store.touch(active, settings.upload_session_ttl_hours)
        response.status_code = 200
        return _init_response(active)

    completing = store.find_completing(payload.fingerprint)
    if completing is not None:
        response.status_code = 202
        return _init_response(completing)

    completed = store.find_completed(payload.fingerprint)
    if completed is not None:
        response.status_code = 200
        return _init_response(completed)

    aborting = store.find_aborting(payload.fingerprint)
    if aborting is not None:
        response.status_code = 202
        return _init_response(aborting)

    document_id = store.new_id()
    object_key = f"documents/{document_id}/source/{filename}"
    if len(object_key.encode("utf-8")) > _MAX_OBJECT_KEY_BYTES:
        raise errors.invalid_filename()

    session = store.create_session(
        fingerprint=payload.fingerprint,
        filename=filename,
        file_type=file_type,
        size_bytes=payload.size_bytes,
        chunk_size=chunk_size,
        total_parts=total_parts,
        object_key=object_key,
        document_id=document_id,
        ttl_hours=settings.upload_session_ttl_hours,
    )
    response.status_code = 201
    return _init_response(session)


@router.put("/{upload_id}/parts/{part_number}", response_model=UploadPartResponse)
async def upload_part(
    upload_id: str,
    part_number: int,
    request: Request,
) -> UploadPartResponse:
    """上传单个分片。

    联调阶段只校验并将分片大小记在进程内，不落对象存储。
    """
    session = store.get(upload_id)
    if session is None:
        raise errors.upload_not_found()
    if session.status == "COMPLETED":
        raise errors.upload_already_completed()
    if session.status == "COMPLETING":
        raise errors.upload_completing()
    if session.status in {"ABORTING", "ABORTED"}:
        raise errors.upload_aborted()
    if session.status == "EXPIRED":
        raise errors.upload_expired()
    if session.status == "FAILED":
        raise errors.upload_failed()
    if session.expires_at <= _now():
        raise errors.upload_expired()
    if part_number < 1 or part_number > session.total_parts:
        raise errors.invalid_part_number(session.total_parts)

    expected = _expected_part_size(session, part_number)
    raw_length = request.headers.get("content-length")
    if raw_length is None:
        raise errors.length_required()
    try:
        content_length = int(raw_length)
    except ValueError as exc:
        raise errors.invalid_part_size(expected, None) from exc
    if content_length > expected:
        raise errors.part_too_large(expected)
    if content_length != expected:
        raise errors.invalid_part_size(expected, content_length)

    body = await request.body()
    if len(body) != expected:
        raise errors.invalid_part_size(expected, len(body))

    store.add_part(session, part_number, len(body))
    return UploadPartResponse(part_number=part_number, size_bytes=len(body))


@router.get("/{upload_id}", response_model=UploadStatusResponse)
def get_upload(upload_id: str) -> UploadStatusResponse:
    """查询上传会话和已上传分片。"""
    session = store.get(upload_id)
    if session is None:
        raise errors.upload_not_found()
    return _status_response(session)


@router.post("/{upload_id}/complete", response_model=UploadCompleteResponse)
def complete_upload(upload_id: str, response: Response) -> UploadCompleteResponse:
    """完成上传并创建联调任务。"""
    session = store.get(upload_id)
    if session is None:
        raise errors.upload_not_found()
    if session.status == "COMPLETED":
        task_id, task_status = _task_status(session)
        response.status_code = 200
        return UploadCompleteResponse(
            upload_id=session.id,
            document_id=session.document_id,
            status=session.status,
            task_id=task_id,
            task_status=task_status,
        )
    if session.status == "COMPLETING":
        response.status_code = 202
        return UploadCompleteResponse(
            upload_id=session.id,
            document_id=session.document_id,
            status=session.status,
        )
    if session.status == "ABORTING":
        raise errors.upload_aborting()
    if session.status == "ABORTED":
        raise errors.upload_aborted()
    if session.status == "EXPIRED":
        raise errors.upload_expired()
    if session.status == "FAILED":
        raise errors.upload_failed()
    if session.expires_at <= _now():
        raise errors.upload_expired()

    missing = [
        number
        for number in range(1, session.total_parts + 1)
        if number not in session.uploaded_parts
    ]
    if missing:
        raise errors.upload_incomplete(missing)

    task = store.mark_completed(session)
    response.status_code = 200
    return UploadCompleteResponse(
        upload_id=session.id,
        document_id=session.document_id,
        status=session.status,
        task_id=task.id,
        task_status=task.status,
    )


@router.delete("/{upload_id}", status_code=204)
def delete_upload(upload_id: str) -> Response:
    """取消上传。"""
    session = store.get(upload_id)
    if session is None:
        raise errors.upload_not_found()
    if session.status == "COMPLETED":
        raise errors.upload_already_completed()
    if session.status == "COMPLETING":
        raise errors.upload_completing()
    store.mark_aborted(session)
    return Response(status_code=204)
