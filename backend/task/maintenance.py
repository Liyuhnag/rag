# -*- coding: utf-8 -*-
"""定时补投 PENDING 解析任务。"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from model.document_task import orm as task_orm
from services.upload.task_dispatch import dispatch_task
from task.app import celery_app

_REDISPATCH_MIN_AGE_SECONDS = 300
_REDISPATCH_BATCH = 100


@celery_app.task(name="rag.redispatch_pending_tasks", ignore_result=True)
def redispatch_pending_tasks() -> None:
    """补投超过 5 分钟仍未执行的 PENDING 任务。"""
    older_than = datetime.now(timezone.utc) - timedelta(
        seconds=_REDISPATCH_MIN_AGE_SECONDS
    )
    for task in task_orm.find_pending(older_than, _REDISPATCH_BATCH):
        dispatch_task(task.id)
