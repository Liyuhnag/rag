# -*- coding: utf-8 -*-
"""异步任务查询。"""

from fastapi import APIRouter

from api.tasks.errors import not_found
from api.tasks.schemas import TaskResponse
from api.tasks.schemas import to_task_response
from model.task import orm as task_orm

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str) -> TaskResponse:
    """按任务 ID 返回处理状态。不存在时返回 404。"""
    task = task_orm.get(task_id)
    if task is None:
        raise not_found()
    return to_task_response(task)
