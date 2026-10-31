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
from services.task_runtime.lifecycle import retry as retry_failed_task

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str) -> TaskResponse:
    """按任务 ID 返回解析状态。不存在时返回 404。"""
    task = document_task_orm.get(task_id)
    if task is None:
        raise not_found()
    return to_task_response(task)


@router.post("/{task_id}/retry", response_model=TaskResponse, status_code=202)
def retry_task(task_id: str) -> TaskResponse:
    """按原阶段重试当前版本的失败任务，投递故障由维护任务补投。"""
    if document_task_orm.get(task_id) is None:
        raise not_found()
    task = retry_failed_task(task_id, datetime.now(timezone.utc))
    if task is None:
        raise cannot_retry()
    return to_task_response(task)
