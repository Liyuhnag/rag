# -*- coding: utf-8 -*-
"""processing_tasks 表的 SQL。"""

from datetime import datetime
from datetime import timezone

from model.common.database import as_aware
from model.common.database import as_utc
from model.common.database import execute
from model.task.model import ProcessingTask


def insert(task: ProcessingTask) -> None:
    """写入一条待处理任务。"""
    execute(
        """
        INSERT INTO processing_tasks (
            id, name, file_type, size_bytes, temp_path, status,
            document_id, error_message, created_at, updated_at
        ) VALUES (
            %(id)s, %(name)s, %(file_type)s, %(size_bytes)s, %(temp_path)s,
            %(status)s, %(document_id)s, %(error_message)s,
            %(created_at)s, %(updated_at)s
        )
        """,
        _params(task),
    )


def get(task_id: str) -> ProcessingTask | None:
    """按 ID 读取任务。不存在时返回 None。"""
    rows = execute(
        "SELECT * FROM processing_tasks WHERE id = %(id)s",
        {"id": task_id},
    )
    if not rows:
        return None
    return _from_row(rows[0])


def set_status(task_id: str, status: str) -> None:
    """更新任务状态。"""
    execute(
        """
        UPDATE processing_tasks
        SET status = %(status)s, updated_at = %(updated_at)s
        WHERE id = %(id)s
        """,
        {
            "id": task_id,
            "status": status,
            "updated_at": as_utc(_now()),
        },
    )


def finish(task_id: str, document_id: str) -> None:
    """对象存储和文档记录都已写入。"""
    execute(
        """
        UPDATE processing_tasks
        SET status = 'STORED', document_id = %(document_id)s,
            updated_at = %(updated_at)s
        WHERE id = %(id)s
        """,
        {
            "id": task_id,
            "document_id": document_id,
            "updated_at": as_utc(_now()),
        },
    )


def fail(task_id: str, message: str) -> None:
    """记下失败原因。"""
    execute(
        """
        UPDATE processing_tasks
        SET status = 'FAILED', error_message = %(error_message)s,
            updated_at = %(updated_at)s
        WHERE id = %(id)s
        """,
        {
            "id": task_id,
            "error_message": message[:1024],
            "updated_at": as_utc(_now()),
        },
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _params(task: ProcessingTask) -> dict[str, object]:
    return {
        "id": task.id,
        "name": task.name,
        "file_type": task.file_type,
        "size_bytes": task.size_bytes,
        "temp_path": task.temp_path,
        "status": task.status,
        "document_id": task.document_id,
        "error_message": task.error_message,
        "created_at": as_utc(task.created_at),
        "updated_at": as_utc(task.updated_at),
    }


def _from_row(row: dict[str, object]) -> ProcessingTask:
    document_id = row["document_id"]
    error_message = row["error_message"]
    return ProcessingTask(
        id=str(row["id"]),
        name=str(row["name"]),
        file_type=str(row["file_type"]),
        size_bytes=int(row["size_bytes"]),
        temp_path=str(row["temp_path"]),
        status=str(row["status"]),
        document_id=None if document_id is None else str(document_id),
        error_message=None if error_message is None else str(error_message),
        created_at=as_aware(row["created_at"]),
        updated_at=as_aware(row["updated_at"]),
    )
