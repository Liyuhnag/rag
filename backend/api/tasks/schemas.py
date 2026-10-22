# -*- coding: utf-8 -*-
"""任务接口的响应。"""

from api.common.schemas import APIModel
from model.document_task.model import DocumentTask
from model.task import ProcessingTask


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


def to_task_response(task: ProcessingTask | DocumentTask) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        status=_value(task.status),
        document_id=getattr(task, "document_id", None),
        error_message=getattr(task, "error_message", None),
        stage=_value(getattr(task, "stage", None)),
        progress=getattr(task, "progress", None),
        step=getattr(task, "step", None),
        version=getattr(task, "version", None),
    )
