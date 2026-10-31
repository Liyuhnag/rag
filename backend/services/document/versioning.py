# -*- coding: utf-8 -*-
"""文档任务遇到版本不匹配时统一收尾，不修改当前文档。"""

from datetime import datetime

from model.document_task import orm as task_orm
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage


def fail_version_mismatch(task: DocumentTask, now: datetime) -> bool:
    """仅结束持有领取标记的旧任务，避免覆盖新版本文档状态。"""
    if not task.lease_token:
        raise ValueError("任务缺少领取标记")
    message = "文档版本不匹配，解析任务已过期"
    if task.stage == DocumentTaskStage.INDEXING:
        message = "文档版本已变化，索引任务已过期"
    return task_orm.supersede(task.id, message, now, task.lease_token)
