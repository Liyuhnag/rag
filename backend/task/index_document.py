# -*- coding: utf-8 -*-
"""索引 worker 自行领取；复用公共租约与失败桥接。"""

import logging
from datetime import datetime
from datetime import timezone

from model.document import orm as document_orm
from model.document.model import DocumentStatus
from model.document_task import orm as task_orm
from model.document_task.lease import LeaseLost
from model.document_task.model import DocumentTaskStage
from services.document.versioning import fail_version_mismatch
from services.indexing.service import DocumentIndexingService
from services.task_runtime.lease import TaskLease
from services.task_runtime.lifecycle import fail
from services.task_runtime.policy import INDEX_STAGES
from services.task_runtime.policy import policy_for
from task.app import celery_app

logger = logging.getLogger("rag.task")
_policy = policy_for(DocumentTaskStage.INDEXING)


@celery_app.task(
    name="rag.index_document",
    ignore_result=True,
    queue="index",
    soft_time_limit=_policy.soft_time_limit,
    time_limit=_policy.time_limit,
)
def index_document(task_id: str) -> None:
    """只有 PENDING + INDEXING 能获得本次执行权。"""
    task = task_orm.claim(task_id, datetime.now(timezone.utc), INDEX_STAGES)
    if task is None:
        return
    try:
        with TaskLease(task.id, task.lease_token, INDEX_STAGES) as lease:
            _run(task, lease)
    except LeaseLost:
        logger.info("索引任务失去执行权，停止写入: %s", task_id)


def _run(task, lease: TaskLease) -> None:
    try:
        # 版本隔离和批次续跑尚未接入。本次保留文档长锁，并先锁任务，
        # 避免外部索引处理到一半被接管，也避免任务行/文档行锁顺序倒置。
        with lease.writes():
            document = document_orm.get(task.document_id)
            if document is None:
                task_orm.fail(task.id, "文档不存在", datetime.now(timezone.utc), lease.token)
                return
            if document.parse_version != task.version:
                fail_version_mismatch(task, datetime.now(timezone.utc))
                return
            if document.status == DocumentStatus.READY:
                task_orm.complete(task.id, "解析与索引完成", datetime.now(timezone.utc), lease.token)
                return

            def heartbeat(completed: int, total: int, last_chunk_id: str) -> None:
                lease.ensure()
                task_orm.update_index_progress(
                    task.id, completed, total, last_chunk_id, lease.token,
                    datetime.now(timezone.utc),
                )

            published = DocumentIndexingService().run(
                document, task.version, heartbeat=heartbeat, lease=lease.ensure,
            )
            if published:
                if not task_orm.complete(
                    task.id, "解析与索引完成", datetime.now(timezone.utc), lease.token
                ):
                    raise LeaseLost(task.id)
            else:
                fail_version_mismatch(task, datetime.now(timezone.utc))
    except LeaseLost:
        raise
    except Exception as exc:
        # 服务调用边界：事务已回滚，重新验证 token 后写入任务和文档失败。
        logger.exception("文档索引失败: %s", task.id)
        fail(task, lease, str(exc) or "索引失败")
        raise
