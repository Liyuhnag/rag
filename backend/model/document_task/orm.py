# -*- coding: utf-8 -*-
"""document_tasks 表的 SQL。"""

from datetime import datetime

from model.common.database import as_utc
from model.common.database import execute
from model.common.database import execute_count
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from model.document_task.model import DocumentTaskStatus


def _from_row(row: dict[str, object]) -> DocumentTask:
    """把一行 document_tasks 记录转换为模型。"""
    return DocumentTask(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        version=int(row["version"]),
        status=DocumentTaskStatus(str(row["status"])),
        stage=DocumentTaskStage(str(row["stage"])),
        progress=int(row["progress"]),
        step=str(row["step"]),
        error_message=(
            None if row["error_message"] is None else str(row["error_message"])
        ),
    )


def get(task_id: str) -> DocumentTask | None:
    """按 taskId 查询任务；不存在返回 None。"""
    rows = execute(
        "SELECT * FROM document_tasks WHERE id = %(id)s LIMIT 1",
        {"id": task_id},
    )
    return None if not rows else _from_row(rows[0])


def find(document_id: str, version: int) -> DocumentTask | None:
    """按 documentId 和 version 查询任务。"""
    rows = execute(
        """
        SELECT * FROM document_tasks
        WHERE document_id = %(document_id)s AND version = %(version)s
        LIMIT 1
        """,
        {"document_id": document_id, "version": version},
    )
    return None if not rows else _from_row(rows[0])


def find_pending(
    older_than: datetime,
    limit: int,
) -> list[DocumentTask]:
    """查询超过最小年龄的 PENDING 任务，用于补投。"""
    rows = execute(
        """
        SELECT * FROM document_tasks
        WHERE status = 'PENDING' AND updated_at < %(older_than)s
        ORDER BY updated_at ASC
        LIMIT %(limit)s
        """,
        {"older_than": as_utc(older_than), "limit": limit},
    )
    return [_from_row(row) for row in rows]


def recover_chunking(
    older_than: datetime,
    now: datetime,
    limit: int,
) -> list[DocumentTask]:
    """条件重置失联的切分/索引任务，返回本次成功恢复的任务用于补投。"""
    rows = execute(
        """
        SELECT * FROM document_tasks
        WHERE status = 'RUNNING' AND stage IN ('CHUNKING', 'INDEXING')
          AND updated_at < %(older_than)s
        ORDER BY updated_at ASC
        LIMIT %(limit)s
        """,
        {"older_than": as_utc(older_than), "limit": limit},
    )
    recovered = []
    for row in rows:
        count = execute_count(
            """
            UPDATE document_tasks
            SET status = 'PENDING', updated_at = %(now)s
            WHERE id = %(id)s AND status = 'RUNNING'
              AND stage IN ('CHUNKING', 'INDEXING') AND updated_at < %(older_than)s
            """,
            {"id": row["id"], "now": as_utc(now), "older_than": as_utc(older_than)},
        )
        if count == 1:
            task = _from_row(row)
            task.status = DocumentTaskStatus.PENDING
            recovered.append(task)
    return recovered


def claim(task_id: str, now: datetime) -> DocumentTask | None:
    """条件抢占 PENDING → RUNNING。"""
    count = execute_count(
        """
        UPDATE document_tasks
        SET status = %(status)s,
            stage = CASE WHEN stage IN ('CHUNKING', 'INDEXING') THEN stage ELSE %(stage)s END,
            progress = 0,
            step = '解析中',
            updated_at = %(now)s
        WHERE id = %(id)s AND status = 'PENDING'
        """,
        {
            "id": task_id,
            "status": DocumentTaskStatus.RUNNING.value,
            "stage": DocumentTaskStage.PARSING.value,
            "now": as_utc(now),
        },
    )
    return None if count != 1 else get(task_id)


def update_progress(
    task_id: str,
    stage: DocumentTaskStage,
    progress: int,
    step: str,
    now: datetime,
) -> None:
    """更新 RUNNING 任务的阶段、进度和步骤。"""
    execute_count(
        """
        UPDATE document_tasks
        SET stage = %(stage)s,
            progress = %(progress)s,
            step = %(step)s,
            updated_at = %(now)s
        WHERE id = %(id)s AND status = 'RUNNING'
        """,
        {
            "id": task_id,
            "stage": stage.value,
            "progress": progress,
            "step": step,
            "now": as_utc(now),
        },
    )


def begin_merge(task_id: str, now: datetime) -> bool:
    """只有一个调度能把解析阶段推进到合并。"""
    count = execute_count(
        """
        UPDATE document_tasks
        SET stage = %(stage)s,
            progress = 80,
            step = '正在合并',
            updated_at = %(now)s
        WHERE id = %(id)s
          AND status = 'RUNNING'
          AND stage = 'PARSING'
        """,
        {
            "id": task_id,
            "stage": DocumentTaskStage.MERGING.value,
            "now": as_utc(now),
        },
    )
    return count == 1


def complete(task_id: str, step: str, now: datetime) -> None:
    """把 RUNNING 任务推进到 COMPLETED/DONE。"""
    execute_count(
        """
        UPDATE document_tasks
        SET status = %(status)s,
            stage = %(stage)s,
            progress = 100,
            step = %(step)s,
            updated_at = %(now)s
        WHERE id = %(id)s AND status = 'RUNNING'
        """,
        {
            "id": task_id,
            "status": DocumentTaskStatus.COMPLETED.value,
            "stage": DocumentTaskStage.DONE.value,
            "step": step,
            "now": as_utc(now),
        },
    )


def fail(task_id: str, message: str, now: datetime) -> None:
    """把 RUNNING 任务标记 FAILED 并记录原因。"""
    execute_count(
        """
        UPDATE document_tasks
        SET status = 'FAILED',
            error_message = %(message)s,
            updated_at = %(now)s
        WHERE id = %(id)s AND status = 'RUNNING'
        """,
        {
            "id": task_id,
            "message": message[:1024],
            "now": as_utc(now),
        },
    )
