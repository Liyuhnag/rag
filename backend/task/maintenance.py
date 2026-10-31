# -*- coding: utf-8 -*-
"""维护任务：补投、过期清理、恢复和孤儿资源清理。"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from config import load_settings
from model.document_task import orm as task_orm
from services.upload.jobs import cleanup
from services.task_runtime.dispatch import dispatch as dispatch_task
from task.app import celery_app

_REDISPATCH_MIN_AGE_SECONDS = 300
_REDISPATCH_BATCH = 100
_CHUNKING_LEASE_SECONDS = 600


@celery_app.task(
    name="rag.expire_uploads",
    ignore_result=True,
    queue="maintenance",
)
def expire_uploads() -> None:
    settings = load_settings()
    cleanup.expire_uploads(
        datetime.now(timezone.utc),
        settings.cleanup_batch_size,
    )


@celery_app.task(
    name="rag.retry_aborting_uploads",
    ignore_result=True,
    queue="maintenance",
)
def retry_aborting_uploads() -> None:
    settings = load_settings()
    now = datetime.now(timezone.utc)
    cleanup.retry_aborting(
        now,
        now - timedelta(seconds=settings.upload_complete_lease_seconds),
        settings.cleanup_batch_size,
    )


@celery_app.task(
    name="rag.recover_completing_uploads",
    ignore_result=True,
    queue="maintenance",
)
def recover_completing_uploads() -> None:
    settings = load_settings()
    now = datetime.now(timezone.utc)
    cleanup.recover_completing_uploads(
        now,
        now - timedelta(seconds=settings.upload_complete_lease_seconds),
        settings.cleanup_batch_size,
        settings.upload_complete_lease_seconds,
    )


@celery_app.task(
    name="rag.cleanup_orphan_mpu",
    ignore_result=True,
    queue="maintenance",
)
def cleanup_orphan_mpu() -> None:
    settings = load_settings()
    cleanup.cleanup_orphan_mpu(
        datetime.now(timezone.utc),
        settings.orphan_mpu_grace_hours,
        settings.cleanup_batch_size,
    )


@celery_app.task(
    name="rag.cleanup_orphan_object",
    ignore_result=True,
    queue="maintenance",
)
def cleanup_orphan_object() -> None:
    settings = load_settings()
    cleanup.cleanup_orphan_object(
        datetime.now(timezone.utc),
        settings.orphan_object_retention_days,
        settings.cleanup_batch_size,
    )


@celery_app.task(
    name="rag.cleanup_old_sessions",
    ignore_result=True,
    queue="maintenance",
)
def cleanup_old_sessions() -> None:
    settings = load_settings()
    cleanup.cleanup_old_sessions(
        datetime.now(timezone.utc),
        settings.session_retention_days,
        settings.cleanup_batch_size,
    )


@celery_app.task(
    name="rag.redispatch_pending_tasks",
    ignore_result=True,
    queue="maintenance",
)
def redispatch_pending_tasks() -> None:
    """恢复失联切分任务，并补投超过 5 分钟仍未执行的 PENDING 任务。"""
    now = datetime.now(timezone.utc)
    recovered = task_orm.recover_chunking(
        now - timedelta(seconds=_CHUNKING_LEASE_SECONDS), now, _REDISPATCH_BATCH
    )
    for task in recovered:
        dispatch_task(task.id, task.stage)
    older_than = now - timedelta(seconds=_REDISPATCH_MIN_AGE_SECONDS)
    for task in task_orm.find_pending(older_than, _REDISPATCH_BATCH):
        dispatch_task(task.id, task.stage)
