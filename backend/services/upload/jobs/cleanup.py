# -*- coding: utf-8 -*-
"""上传相关清理任务。"""

import logging
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from model.document import orm as document_orm
from model.upload import orm as upload_orm
from services.common.exceptions import StorageError
from services.common.object_storage import client as storage_client
from services.upload.steps.completion import recover_completing

logger = logging.getLogger("rag.cleanup")

_TERMINAL_SESSION_STATUSES = {
    "COMPLETED",
    "ABORTED",
    "EXPIRED",
    "FAILED",
}


def expire_uploads(now: datetime, limit: int) -> int:
    """过期 UPLOADING → ABORTING(EXPIRED) → abort → EXPIRED。"""
    count = 0
    for session in upload_orm.find_expired_uploading(now, limit):
        if not upload_orm.mark_aborting(session.id, "EXPIRED", now):
            continue
        try:
            storage_client().abort_multipart_upload(
                session.object_key,
                session.s3_upload_id,
            )
        except StorageError:
            logger.exception("过期会话取消失败: %s", session.id)
            continue
        upload_orm.finish_abort(session.id, "EXPIRED", now)
        count += 1
    return count


def retry_aborting(
    now: datetime,
    older_than: datetime,
    limit: int,
) -> int:
    """重试超时的 ABORTING 会话。"""
    count = 0
    for session in upload_orm.find_aborting_before(older_than, limit):
        try:
            storage_client().abort_multipart_upload(
                session.object_key,
                session.s3_upload_id,
            )
        except StorageError:
            logger.exception("重试取消失败: %s", session.id)
            continue
        status = "EXPIRED" if session.abort_reason == "EXPIRED" else "ABORTED"
        upload_orm.finish_abort(session.id, status, now)
        count += 1
    return count


def recover_completing_uploads(
    now: datetime,
    older_than: datetime,
    limit: int,
    lease_seconds: int,
) -> int:
    """恢复超时的 COMPLETING 会话。"""
    count = 0
    for session in upload_orm.find_completing_before(older_than, limit):
        try:
            result = recover_completing(session, lease_seconds)
        except StorageError:
            logger.exception("COMPLETING 恢复失败: %s", session.id)
            continue
        if result is not None:
            count += 1
    return count


def cleanup_orphan_mpu(
    now: datetime,
    grace_hours: int,
    limit: int,
) -> int:
    """取消没有会话或会话已终态且超过宽限期的 MPU。"""
    try:
        uploads = storage_client().list_multipart_uploads("documents/")
    except StorageError:
        logger.exception("列出 Multipart Upload 失败")
        return 0

    grace = timedelta(hours=grace_hours)
    min_age = timedelta(hours=1)
    count = 0
    for upload in uploads:
        if count >= limit:
            break
        initiated = upload.initiated
        if initiated is None:
            continue
        if initiated.tzinfo is None:
            initiated = initiated.replace(tzinfo=timezone.utc)
        if initiated + grace > now or initiated + min_age > now:
            continue

        session = upload_orm.find_by_s3_upload_id(upload.upload_id)
        if session is not None:
            if session.status not in _TERMINAL_SESSION_STATUSES:
                continue
            if session.updated_at + grace > now:
                continue

        try:
            storage_client().abort_multipart_upload(
                upload.key,
                upload.upload_id,
            )
        except StorageError:
            logger.exception("清理孤儿 MPU 失败: %s", upload.upload_id)
            continue
        count += 1
    return count


def cleanup_orphan_object(
    now: datetime,
    retention_days: int,
    limit: int,
) -> int:
    """删除终态会话对应、但没有 documents 行的对象。"""
    older_than = now - timedelta(days=retention_days)
    count = 0
    for session in upload_orm.find_terminal_before(older_than, limit):
        if document_orm.exists(session.document_id):
            logger.warning(
                "对象仍被文档引用，跳过清理: %s",
                session.object_key,
            )
            continue
        try:
            size = storage_client().head_object(session.object_key)
        except StorageError:
            logger.exception("查询孤儿对象失败: %s", session.object_key)
            continue
        if size is None:
            continue
        try:
            storage_client().delete(session.object_key)
        except StorageError:
            logger.exception("删除孤儿对象失败: %s", session.object_key)
            continue
        count += 1
    return count


def cleanup_old_sessions(
    now: datetime,
    retention_days: int,
    limit: int,
) -> int:
    """删除超过保留期的终态会话行。"""
    older_than = now - timedelta(days=retention_days)
    return upload_orm.delete_old_sessions(older_than, limit)
