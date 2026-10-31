# -*- coding: utf-8 -*-
"""公共任务交接与文档状态桥接。"""

from collections.abc import Callable
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from model.document import orm as document_orm
from model.document_task import orm as task_orm
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from model.parse_job import orm as parse_job_orm
from services.task_runtime.dispatch import dispatch
from services.task_runtime.lease import TaskLease
from services.task_runtime.policy import POLICIES
from services.task_runtime.policy import policy_for


def advance(
    lease: TaskLease,
    next_stage: DocumentTaskStage,
    progress: int,
    step: str,
    prepare: Callable[[], bool | None] | None = None,
) -> bool:
    """交接先持久化并撤销 token，退出事务后再投递。"""
    policy_for(next_stage)
    with lease.writes():
        if prepare is not None and prepare() is False:
            return False
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


def recover(now: datetime, limit: int) -> int:
    """按阶段空闲阈值收回失联执行权，数据库更新提交后再投递。"""
    cutoffs = {
        stage: now - timedelta(seconds=policy.idle_seconds)
        for stage, policy in POLICIES.items()
        if stage != DocumentTaskStage.PENDING
    }
    recovered = task_orm.recover_stale(cutoffs, now, limit)
    for task in recovered:
        dispatch(task.id, task.stage)
    return len(recovered)


def retry(task_id: str, now: datetime) -> DocumentTask | None:
    """通过统一准入后按原阶段重跑；准备与排队同事务，投递在提交后。"""
    def prepare(task: DocumentTask) -> None:
        if task.stage in (DocumentTaskStage.PARSING, DocumentTaskStage.MERGING):
            parse_job_orm.retry_failed_parts(task.id, task.document_id, task.version, now)

    task = task_orm.retry_failed(task_id, now, tuple(POLICIES), prepare)
    if task is not None:
        dispatch(task.id, task.stage)
    return task
