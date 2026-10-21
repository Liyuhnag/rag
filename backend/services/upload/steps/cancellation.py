# -*- coding: utf-8 -*-
"""取消上传。"""

from datetime import datetime
from datetime import timezone

from model.upload import orm as upload_orm
from services.common.object_storage import client as storage_client
from services.upload.errors import upload_completed
from services.upload.errors import upload_completing
from services.upload.shared import require_session


def delete_upload(upload_id: str) -> None:
    """取消上传并释放 Multipart Upload。"""
    session = require_session(upload_id)
    if session.status == "COMPLETED":
        raise upload_completed()
    if session.status == "COMPLETING":
        raise upload_completing()
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
