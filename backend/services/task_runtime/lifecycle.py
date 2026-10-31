# -*- coding: utf-8 -*-
"""公共任务交接与文档状态桥接。"""

from datetime import datetime
from datetime import timezone

from model.document import orm as document_orm
from model.document_task import orm as task_orm
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from services.task_runtime.dispatch import dispatch
from services.task_runtime.lease import TaskLease
from services.task_runtime.policy import policy_for


def advance(
    lease: TaskLease,
    next_stage: DocumentTaskStage,
    progress: int,
    step: str,
) -> bool:
    """交接先持久化并撤销 token，退出事务后再投递。"""
    policy_for(next_stage)
    with lease.writes():
        changed = task_orm.advance(
            lease.task_id, lease.token, next_stage, progress, step,
            datetime.now(timezone.utc), lease.stages,
        )
    if changed:
        dispatch(lease.task_id, next_stage)
    return changed


def fail(task: DocumentTask, lease: TaskLease, message: str) -> bool:
    """任务行 → 文档行，在同一事务内结束任务并更新当前文档。"""
    now = datetime.now(timezone.utc)
    with lease.writes():
        changed = task_orm.fail(task.id, message, now, lease.token)
        if changed:
            document_orm.mark_failed(task.document_id, message, now, expected_version=task.version)
    return changed
