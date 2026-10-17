# -*- coding: utf-8 -*-
"""接收上传内容，生成异步处理任务。"""

from datetime import datetime
from datetime import timezone
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

from task import record
from task.record import ProcessingTask
from task.runner import submit


def accept_upload(name: str, file_type: str, content: bytes) -> ProcessingTask:
    """把文件落到临时路径并提交处理任务。"""
    task_id = uuid4().hex
    now = datetime.now(timezone.utc)
    path = _temp_path(task_id)
    path.write_bytes(content)
    task = ProcessingTask(
        id=task_id,
        name=name,
        file_type=file_type,
        size_bytes=len(content),
        temp_path=str(path),
        status="PENDING",
        document_id=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    record.insert(task)
    submit(task_id)
    stored = record.get(task_id)
    return stored if stored is not None else task


def _temp_path(task_id: str) -> Path:
    directory = Path(gettempdir()) / "rag-uploads"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / task_id
