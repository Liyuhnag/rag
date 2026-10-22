# -*- coding: utf-8 -*-
"""任务接口的响应。"""

from api.common.schemas import APIModel
from model.document_task.model import DocumentTask


class TaskResponse(APIModel):
    id: str
    status: str
    document_id: str | None = None
    error_message: str | None = None
    stage: str | None = None
    progress: int | None = None
    step: str | None = None
    version: int | None = None


def _value(value):
    return getattr(value, "value", value)


def to_task_response(task: DocumentTask) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        status=_value(task.status),
        document_id=task.document_id,
        error_message=task.error_message,
        stage=_value(task.stage),
        progress=task.progress,
        step=task.step,
        version=task.version,
    )
