# -*- coding: utf-8 -*-
"""合并 worker 自行领取；复用公共租约与失败桥接。"""

import logging
from datetime import datetime
from datetime import timezone

from model.document import orm as document_orm
from model.document_task import orm as task_orm
from model.document_task.lease import LeaseLost
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from services.document.versioning import fail_version_mismatch
from services.merge.service import run_merge
from services.task_runtime.lease import TaskLease
from services.task_runtime.lifecycle import fail
from services.task_runtime.policy import MERGE_STAGES
from services.task_runtime.policy import policy_for
from task.app import celery_app

logger = logging.getLogger("rag.task")
_policy = policy_for(DocumentTaskStage.MERGING)


@celery_app.task(
    name="rag.merge_document",
    ignore_result=True,
    queue=_policy.queue,
    soft_time_limit=_policy.soft_time_limit,
    time_limit=_policy.time_limit,
)
def merge_document(task_id: str) -> None:
    """只有 PENDING + MERGING 能获得本次执行权。"""
    task = task_orm.claim(task_id, datetime.now(timezone.utc), MERGE_STAGES)
    if task is None:
        return
    if not task.lease_token:
        logger.error("任务缺少领取标记: %s", task_id)
        return
    try:
        with TaskLease(task.id, task.lease_token, MERGE_STAGES) as lease:
            _run(task, lease)
    except LeaseLost:
        logger.info("合并任务失去执行权，停止写入: %s", task_id)


def _run(task: DocumentTask, lease: TaskLease) -> None:
    now = datetime.now(timezone.utc)
    document = document_orm.get(task.document_id)
    if document is None:
        with lease.writes():
            task_orm.fail(task.id, "文档不存在", now, lease.token)
        return
    try:
        with lease.writes():
            if document.parse_version != task.version:
                fail_version_mismatch(task, now)
                return
        run_merge(task, document, lease)
    except LeaseLost:
        raise
    except Exception as exc:
        logger.exception("文档合并失败: %s", task.id)
        fail(task, lease, str(exc) or "合并失败")
        raise
