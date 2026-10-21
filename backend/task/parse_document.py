# -*- coding: utf-8 -*-
"""document_tasks 的解析 Worker。

真正的文本解析/分块/索引还没接入；当前只做对象存在性和大小校验，
把任务从 RUNNING 推进到 COMPLETED。接入解析器后替换校验后的处理。
"""

import logging
from datetime import datetime
from datetime import timezone

from model.document import orm as document_orm
from model.document_task import orm as task_orm
from services.common.object_storage import client as storage_client
from task.app import celery_app

logger = logging.getLogger("rag.task")


@celery_app.task(
    name="rag.parse_document",
    ignore_result=True,
    soft_time_limit=300,
    time_limit=330,
)
def parse_document(task_id: str) -> None:
    """条件抢占任务，校验对象后完成。"""
    now = datetime.now(timezone.utc)
    task = task_orm.claim(task_id, now)
    if task is None:
        return

    try:
        document = document_orm.get(task.document_id)
        if document is None:
            raise RuntimeError("文档不存在")
        size = storage_client().head_object(document.object_key)
        if size is None:
            raise RuntimeError("对象不存在")
        if size != document.size_bytes:
            raise RuntimeError("对象大小不一致")
        task_orm.complete(task_id, "文件已校验，解析器待接入", now)
    except Exception as exc:
        message = str(exc) or "解析失败"
        logger.exception("解析任务失败: %s", task_id)
        task_orm.fail(task_id, message, now)
        raise
