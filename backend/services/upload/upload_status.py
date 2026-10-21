# -*- coding: utf-8 -*-
"""查询上传状态。"""

from model.document_task.model import DocumentTask
from model.upload import orm as upload_orm
from model.upload.model import UploadSession
from services.common.object_storage import UploadedPart
from services.upload.errors import UploadError
from services.upload.upload_completion import recover_completing
from services.upload.upload_shared import parts_or_none
from services.upload.upload_shared import task_for


def get_upload(
    upload_id: str,
) -> tuple[UploadSession, list[UploadedPart], DocumentTask | None]:
    """查询会话、已上传分片和当前任务。"""
    session = upload_orm.get(upload_id)
    if session is None:
        raise UploadError(404, "UPLOAD_NOT_FOUND", "上传会话不存在")

    if session.status == "COMPLETING":
        recovered = recover_completing(session)
        if recovered is not None:
            return recovered.session, [], recovered.task

    parts: list[UploadedPart] = []
    closed = {"COMPLETED", "ABORTING", "ABORTED", "EXPIRED", "FAILED"}
    if session.status not in closed:
        parts = parts_or_none(session) or []
    return session, parts, task_for(session)
