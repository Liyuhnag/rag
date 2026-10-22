# -*- coding: utf-8 -*-
"""大文件上传接口。"""

import re

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
from services.common.exceptions import FingerprintConflict
from services.upload import service as upload_service
from services.upload.errors import UploadError

router = APIRouter(prefix="/uploads", tags=["uploads"])

_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_TOTAL_PARTS = 10000
_MAX_OBJECT_KEY_BYTES = 1024


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


def _app_error(exc: UploadError) -> AppError:
    return AppError(
        exc.status_code,
        exc.code,
        exc.message,
        exc.details,
    )


def _task_status_value(task) -> str | None:
    if task is None:
        return None
    return getattr(task.status, "value", task.status)


def _init_response(session, uploaded_parts, task=None) -> UploadInitResponse:
    return UploadInitResponse(
        upload_id=session.id,
        document_id=session.document_id,
        status=session.status,
        chunk_size=session.chunk_size,
        total_parts=session.total_parts,
        uploaded_parts=[
            UploadedPart(
                part_number=part.part_number,
                size_bytes=part.size_bytes,
            )
            for part in uploaded_parts
        ],
        expires_at=session.expires_at,
        task_id=None if task is None else task.id,
        task_status=_task_status_value(task),
    )


def _status_response(session, uploaded_parts, task) -> UploadStatusResponse:
    return UploadStatusResponse(
        upload_id=session.id,
        document_id=session.document_id,
        filename=session.filename,
        size_bytes=session.size_bytes,
        chunk_size=session.chunk_size,
        total_parts=session.total_parts,
        status=session.status,
        uploaded_parts=[
            UploadedPart(
                part_number=part.part_number,
                size_bytes=part.size_bytes,
            )
            for part in uploaded_parts
        ],
        expires_at=session.expires_at,
        error_message=session.error_message,
        task_id=None if task is None else task.id,
        task_status=_task_status_value(task),
    )


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

    preview_key = f"documents/{'0' * 32}/source/{filename}"
    if len(preview_key.encode("utf-8")) > _MAX_OBJECT_KEY_BYTES:
        raise errors.invalid_filename()

    try:
        result = upload_service.init_upload(
            fingerprint=payload.fingerprint,
            filename=filename,
            file_type=file_type,
            size_bytes=payload.size_bytes,
            chunk_size=chunk_size,
            total_parts=total_parts,
            ttl_hours=settings.upload_session_ttl_hours,
        )
    except FingerprintConflict as exc:
        raise errors.fingerprint_conflict(exc.upload_id) from exc
    except UploadError as exc:
        raise _app_error(exc) from exc

    if result.outcome == "created":
        response.status_code = 201
    elif result.outcome in {"completing", "aborting"}:
        response.status_code = 202
    else:
        response.status_code = 200

    return _init_response(
        result.session,
        result.uploaded_parts,
        result.task,
    )


@router.put("/{upload_id}/parts/{part_number}", response_model=UploadPartResponse)
async def upload_part(
    upload_id: str,
    part_number: int,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> UploadPartResponse:
    """上传单个分片。"""
    raw_length = request.headers.get("content-length")
    content_length: int | None = None
    body = b""

    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError:
            content_length = -1
        if content_length > settings.upload_chunk_bytes:
            raise errors.part_too_large(settings.upload_chunk_bytes)
        if content_length >= 0:
            body = await request.body()

    try:
        number, size = upload_service.upload_part(
            upload_id=upload_id,
            part_number=part_number,
            content_length=content_length,
            body=body,
        )
    except UploadError as exc:
        raise _app_error(exc) from exc

    return UploadPartResponse(part_number=number, size_bytes=size)


@router.get("/{upload_id}", response_model=UploadStatusResponse)
def get_upload(upload_id: str) -> UploadStatusResponse:
    """查询上传会话和已上传分片。"""
    try:
        session, parts, task = upload_service.get_upload(upload_id)
    except UploadError as exc:
        raise _app_error(exc) from exc
    return _status_response(session, parts, task)


@router.post("/{upload_id}/complete", response_model=UploadCompleteResponse)
def complete_upload(upload_id: str, response: Response) -> UploadCompleteResponse:
    """完成上传并写入文档和任务。"""
    try:
        result = upload_service.complete_upload(upload_id)
    except UploadError as exc:
        raise _app_error(exc) from exc

    response.status_code = 202 if result.outcome == "completing" else 200
    return UploadCompleteResponse(
        upload_id=result.session.id,
        document_id=result.session.document_id,
        status=result.session.status,
        task_id=None if result.task is None else result.task.id,
        task_status=_task_status_value(result.task),
    )


@router.delete("/{upload_id}", status_code=204)
def delete_upload(upload_id: str) -> Response:
    """取消上传。"""
    try:
        upload_service.delete_upload(upload_id)
    except UploadError as exc:
        raise _app_error(exc) from exc
    return Response(status_code=204)
