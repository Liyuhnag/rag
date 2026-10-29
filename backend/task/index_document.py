# -*- coding: utf-8 -*-
"""独立索引队列：MySQL 子块 → embedding → Milvus + Elasticsearch。"""

import logging
from datetime import datetime
from datetime import timezone

from model.document import orm as document_orm
from model.document_task import orm as task_orm
from model.document_task.model import DocumentTaskStage
from model.document_task.model import DocumentTaskStatus
from services.indexing.service import DocumentIndexingService
from task.app import celery_app

logger = logging.getLogger("rag.task")


@celery_app.task(
    name="rag.index_document",
    ignore_result=True,
    queue="index",
    soft_time_limit=1740,
    time_limit=1800,
)
def index_document(task_id: str) -> None:
    """消费已领取任务，每批续租；失联任务沿现有维护流程补投。"""
    task = task_orm.get(task_id)
    if (
        task is None
        or task.status != DocumentTaskStatus.RUNNING
        or task.stage != DocumentTaskStage.INDEXING
    ):
        return
    document = document_orm.get(task.document_id)
    if document is None:
        task_orm.fail(task_id, "文档不存在", datetime.now(timezone.utc))
        return
    if document.parse_version != task.version:
        return

    def heartbeat(completed: int, total: int, last_chunk_id: str):
        task_orm.update_index_progress(
            task_id, completed, total, last_chunk_id, datetime.now(timezone.utc)
        )

    def lease():
        task_orm.touch_indexing(task_id, datetime.now(timezone.utc))

    try:
        if DocumentIndexingService().run(
            document, task.version, heartbeat=heartbeat, lease=lease
        ):
            task_orm.complete(task_id, "解析与索引完成", datetime.now(timezone.utc))
    except Exception as exc:
        # 外部存储边界统一记录失败，保留 MySQL 子块供重新索引。
        logger.exception("文档索引失败: %s", task_id)
        now = datetime.now(timezone.utc)
        message = str(exc) or "索引失败"
        task_orm.fail(task_id, message, now)
        document_orm.mark_failed(
            document.id, message, now, expected_version=task.version
        )
        raise
