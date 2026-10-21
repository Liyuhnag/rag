# -*- coding: utf-8 -*-
"""把 document_tasks 投递到 Celery。"""

import logging

from task.parse_document import parse_document

logger = logging.getLogger("rag.task")


def dispatch_task(task_id: str) -> bool:
    """投递失败只记录日志，任务保持 PENDING 等补投。"""
    try:
        parse_document.delay(task_id)
    except Exception:
        logger.exception("解析任务投递失败: %s", task_id)
        return False
    return True
