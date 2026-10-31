# -*- coding: utf-8 -*-
"""异步任务查询。"""

from datetime import datetime
from datetime import timezone

from fastapi import APIRouter

from api.tasks.errors import not_found
from api.tasks.errors import cannot_retry
from api.tasks.schemas import TaskResponse
from api.tasks.schemas import to_task_response
from model.document_task import orm as document_task_orm
from services.task_runtime.dispatch import dispatch as dispatch_task

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str) -> TaskResponse:
    """按任务 ID 返回解析状态。不存在时返回 404。"""
    task = document_task_orm.get(task_id)
    if task is None:
        raise not_found()
    return to_task_response(task)


@router.post("/{task_id}/retry", response_model=TaskResponse, status_code=202)
def retry_indexing(task_id: str) -> TaskResponse:
    """重新投递当前版本失败的索引任务。投递故障由维护任务补投。"""
    if document_task_orm.get(task_id) is None:
        raise not_found()
    if not document_task_orm.retry_failed_indexing(task_id, datetime.now(timezone.utc)):
        raise cannot_retry()
    task = document_task_orm.get(task_id)
    if task is None:
        raise not_found()
    dispatch_task(task.id, task.stage)
    return to_task_response(task)
