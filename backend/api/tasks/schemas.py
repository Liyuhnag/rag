# -*- coding: utf-8 -*-
"""任务接口的响应。"""

from api.common.schemas import APIModel
from model.task import ProcessingTask


class TaskResponse(APIModel):
    id: str
    status: str
    document_id: str | None = None
    error_message: str | None = None


def to_task_response(task: ProcessingTask) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        status=task.status,
        document_id=task.document_id,
        error_message=task.error_message,
    )
