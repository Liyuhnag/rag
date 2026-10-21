# -*- coding: utf-8 -*-
"""任务接口的响应。"""

from api.common.schemas import APIModel
from model.task import ProcessingTask
from services.upload.store import MockTask


class TaskResponse(APIModel):
    id: str
    status: str
    document_id: str | None = None
    error_message: str | None = None
    stage: str | None = None
    progress: int | None = None
    step: str | None = None
    version: int | None = None


def to_task_response(task: ProcessingTask | MockTask) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        status=task.status,
        document_id=getattr(task, "document_id", None),
        error_message=getattr(task, "error_message", None),
        stage=getattr(task, "stage", None),
        progress=getattr(task, "progress", None),
        step=getattr(task, "step", None),
        version=getattr(task, "version", None),
    )
