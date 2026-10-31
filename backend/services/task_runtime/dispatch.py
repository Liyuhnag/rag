# -*- coding: utf-8 -*-
"""所有阶段共用的投递边界；失败不修改数据库。"""

import logging

from model.document_task.model import DocumentTaskStage
from services.task_runtime.policy import policy_for

logger = logging.getLogger("rag.task")


def dispatch(task_id: str, stage: DocumentTaskStage) -> bool:
    """消息只提供定位信息，执行权由目标 worker 自行领取。"""
    policy = policy_for(stage)
    # 延迟导入，避免上传、Celery 任务注册与运行时之间形成导入环。
    from task.index_document import index_document
    from task.parse_document import parse_document

    worker = index_document if policy.entry == "index_document" else parse_document
    try:
        worker.apply_async(
            args=[task_id], queue=policy.queue,
            soft_time_limit=policy.soft_time_limit, time_limit=policy.time_limit,
        )
    except Exception:
        # broker 是外部边界，数据库中的 PENDING 由维护任务补投。
        logger.exception("任务投递失败: %s stage=%s", task_id, stage)
        return False
    return True
