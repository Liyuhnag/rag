# -*- coding: utf-8 -*-
"""上传单个分片。"""

from datetime import datetime
from datetime import timezone

from model.upload import orm as upload_orm
from model.upload.model import UploadSession
from services.common.exceptions import UploadNotFound
from services.common.object_storage import client as storage_client
from services.upload.errors import UploadError
from services.upload.upload_shared import part_size

_CLOSED_PART_UPLOAD: dict[str, tuple[int, str, str]] = {
    "COMPLETED": (409, "UPLOAD_ALREADY_COMPLETED", "上传已完成"),
    "COMPLETING": (409, "UPLOAD_COMPLETING", "上传正在完成"),
    "ABORTING": (410, "UPLOAD_ABORTED", "上传已取消"),
    "ABORTED": (410, "UPLOAD_ABORTED", "上传已取消"),
    "EXPIRED": (410, "UPLOAD_EXPIRED", "上传已过期"),
    "FAILED": (409, "UPLOAD_FAILED", "上传已失败，请重新上传"),
}


def upload_part(
    *,
    upload_id: str,
    part_number: int,
    content_length: int | None,
    body: bytes,
) -> tuple[int, int]:
    """校验分片并写入 S3。"""
    session = upload_orm.get(upload_id)
    if session is None:
        raise UploadError(404, "UPLOAD_NOT_FOUND", "上传会话不存在")

    now = datetime.now(timezone.utc)
    _reject_closed_upload(session, now)
    _require_part_body(session, part_number, content_length, body)

    try:
        storage_client().upload_part(
            session.object_key,
            session.s3_upload_id,
            part_number,
            body,
        )
    except UploadNotFound as exc:
        upload_orm.mark_failed(session.id, "分片上传已不存在", now)
        raise UploadError(410, "UPLOAD_EXPIRED", "上传已过期") from exc
    return part_number, len(body)


def _reject_closed_upload(session: UploadSession, now: datetime) -> None:
    """已经结束或过期的会话不能再接收分片。"""
    closed = _CLOSED_PART_UPLOAD.get(session.status)
    if closed is not None:
        raise UploadError(*closed)
    if session.expires_at <= now:
        raise UploadError(410, "UPLOAD_EXPIRED", "上传已过期")


def _require_part_body(
    session: UploadSession,
    part_number: int,
    content_length: int | None,
    body: bytes,
) -> None:
    """分片编号和正文长度必须与会话约定一致。"""
    if part_number < 1 or part_number > session.total_parts:
        raise UploadError(
            422,
            "INVALID_PART_NUMBER",
            "分片编号不合法",
            {"min": 1, "max": session.total_parts},
        )
    expected = part_size(session, part_number)
    if content_length is None:
        raise UploadError(411, "LENGTH_REQUIRED", "缺少 Content-Length")
    if content_length < 0:
        raise UploadError(
            422,
            "INVALID_PART_SIZE",
            "分片大小与预期不符",
            {"expected": expected, "actual": content_length},
        )
    if content_length > expected:
        raise UploadError(
            413,
            "PART_TOO_LARGE",
            "分片超过预期大小",
            {"expected": expected},
        )
    if content_length != expected or len(body) != expected:
        raise UploadError(
            422,
            "INVALID_PART_SIZE",
            "分片大小与预期不符",
            {"expected": expected, "actual": len(body)},
        )
