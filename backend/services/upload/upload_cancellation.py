# -*- coding: utf-8 -*-
"""取消上传。"""

from datetime import datetime
from datetime import timezone

from model.upload import orm as upload_orm
from services.common.object_storage import client as storage_client
from services.upload.errors import UploadError


def delete_upload(upload_id: str) -> None:
    """取消上传并释放 Multipart Upload。"""
    session = upload_orm.get(upload_id)
    if session is None:
        raise UploadError(404, "UPLOAD_NOT_FOUND", "上传会话不存在")
    if session.status == "COMPLETED":
        raise UploadError(409, "UPLOAD_ALREADY_COMPLETED", "上传已完成")
    if session.status == "COMPLETING":
        raise UploadError(409, "UPLOAD_COMPLETING", "上传正在完成")
    if session.status in {"ABORTED", "EXPIRED"}:
        return

    now = datetime.now(timezone.utc)
    if session.status == "UPLOADING":
        if not upload_orm.mark_aborting(session.id, "USER", now):
            return
    storage_client().abort_multipart_upload(
        session.object_key,
        session.s3_upload_id,
    )
    upload_orm.finish_abort(session.id, "ABORTED", now)
