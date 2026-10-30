# -*- coding: utf-8 -*-
"""document_tasks 表的 SQL。"""

import json
from datetime import datetime
from uuid import uuid4

from model.common.database import as_utc
from model.common.database import execute
from model.common.database import execute_count
from model.common.database import transaction
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
        lease_token=_lease_token(row.get("result_json")),
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
    """条件抢占 PENDING → RUNNING，并换成新的领取标记。"""
    count = execute_count(
        """
        UPDATE document_tasks
        SET status = %(status)s,
            stage = CASE WHEN stage IN ('CHUNKING', 'INDEXING') THEN stage ELSE %(stage)s END,
            progress = 0,
            step = '解析中',
            result_json = JSON_SET(
                COALESCE(result_json, JSON_OBJECT()),
                '$.lease_token', %(lease_token)s
            ),
            updated_at = %(now)s
        WHERE id = %(id)s AND status = 'PENDING'
        """,
        {
            "id": task_id,
            "status": DocumentTaskStatus.RUNNING.value,
            "stage": DocumentTaskStage.PARSING.value,
            "lease_token": uuid4().hex,
            "now": as_utc(now),
        },
    )
    return None if count != 1 else get(task_id)


def install_index_lease(task_id: str, now: datetime) -> str | None:
    """给尚未标记的索引任务写上领取标记。写不上说明这次领取不属于当前执行。"""
    token = uuid4().hex
    count = execute_count(
        """
        UPDATE document_tasks
        SET result_json = JSON_SET(
                COALESCE(result_json, JSON_OBJECT()),
                '$.lease_token', %(token)s
            ),
            updated_at = %(now)s
        WHERE id = %(id)s AND status = 'RUNNING' AND stage = 'INDEXING'
          AND JSON_EXTRACT(result_json, '$.lease_token') IS NULL
        """,
        {"id": task_id, "token": token, "now": as_utc(now)},
    )
    return token if count == 1 else None


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


def update_index_progress(
    task_id: str,
    completed: int,
    total: int,
    last_chunk_id: str,
    lease_token: str,
    now: datetime,
) -> None:
    """双索引刷新并核对通过后，记录已索引条数并续租。"""
    progress = 90 + min(9, 9 * completed // total)
    execute_count(
        """
        UPDATE document_tasks
        SET progress = %(progress)s,
            step = %(step)s,
            result_json = JSON_SET(
                COALESCE(result_json, JSON_OBJECT()),
                '$.embedded_count', %(completed)s,
                '$.es_indexed', %(completed)s,
                '$.milvus_indexed', %(completed)s,
                '$.last_chunk_id', %(last_chunk_id)s
            ),
            updated_at = %(now)s
        WHERE id = %(id)s AND status = 'RUNNING' AND stage = 'INDEXING'
          AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.lease_token')) = %(lease_token)s
        """,
        {
            "id": task_id,
            "progress": progress,
            "step": f"已向量化 {completed}/{total}",
            "completed": completed,
            "last_chunk_id": last_chunk_id,
            "lease_token": lease_token,
            "now": as_utc(now),
        },
    )


def touch_indexing(task_id: str, lease_token: str, now: datetime) -> None:
    """这次领取还在索引时续租，不提前声明 ES 写入成功。"""
    execute_count(
        """
        UPDATE document_tasks SET updated_at = %(now)s
        WHERE id = %(id)s AND status = 'RUNNING' AND stage = 'INDEXING'
          AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.lease_token')) = %(lease_token)s
        """,
        {"id": task_id, "lease_token": lease_token, "now": as_utc(now)},
    )


def retry_failed_indexing(task_id: str, now: datetime) -> bool:
    """仅允许当前文档版本的失败索引重新排队。"""
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT t.status, t.stage, t.version, d.parse_version,
                       d.status AS document_status
                FROM document_tasks AS t
                JOIN documents AS d ON d.id = t.document_id
                WHERE t.id = %(id)s FOR UPDATE
                """,
                {"id": task_id},
            )
            row = cursor.fetchone()
            if (
                row is None
                or row["status"] != "FAILED"
                or row["stage"] != "INDEXING"
                or row["document_status"] != "FAILED"
                or int(row["version"]) != int(row["parse_version"])
            ):
                return False
            cursor.execute(
                """
                UPDATE documents AS d
                JOIN document_tasks AS t ON t.document_id = d.id
                SET d.status = 'PROCESSING', d.error_message = NULL,
                    d.updated_at = %(now)s
                WHERE t.id = %(id)s
                """,
                {"id": task_id, "now": as_utc(now)},
            )
            cursor.execute(
                """
                UPDATE document_tasks
                SET status = 'PENDING', progress = 90,
                    step = '等待重新索引', error_message = NULL,
                    result_json = JSON_REMOVE(
                        COALESCE(result_json, JSON_OBJECT()),
                        '$.embedded_count', '$.es_indexed',
                        '$.milvus_indexed', '$.last_chunk_id'
                    ),
                    updated_at = %(now)s
                WHERE id = %(id)s
                """,
                {"id": task_id, "now": as_utc(now)},
            )
    return True


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


def complete(
    task_id: str,
    step: str,
    now: datetime,
    lease_token: str | None = None,
) -> bool:
    """把 RUNNING 任务推进到 COMPLETED/DONE。带标记时只完成这次领取。"""
    sql = """
        UPDATE document_tasks
        SET status = %(status)s,
            stage = %(stage)s,
            progress = 100,
            step = %(step)s,
            updated_at = %(now)s
        WHERE id = %(id)s AND status = 'RUNNING'
    """
    params: dict[str, object] = {
        "id": task_id,
        "status": DocumentTaskStatus.COMPLETED.value,
        "stage": DocumentTaskStage.DONE.value,
        "step": step,
        "now": as_utc(now),
    }
    if lease_token is not None:
        sql += (
            " AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.lease_token'))"
            " = %(lease_token)s"
        )
        params["lease_token"] = lease_token
    return execute_count(sql, params) == 1


def fail(
    task_id: str,
    message: str,
    now: datetime,
    lease_token: str | None = None,
) -> bool:
    """把 RUNNING 任务标记 FAILED。带标记时只失败这次领取。"""
    sql = """
        UPDATE document_tasks
        SET status = 'FAILED',
            error_message = %(message)s,
            updated_at = %(now)s
        WHERE id = %(id)s AND status = 'RUNNING'
    """
    params: dict[str, object] = {
        "id": task_id,
        "message": message[:1024],
        "now": as_utc(now),
    }
    if lease_token is not None:
        sql += (
            " AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.lease_token'))"
            " = %(lease_token)s"
        )
        params["lease_token"] = lease_token
    return execute_count(sql, params) == 1


def _lease_token(value: object) -> str | None:
    """从 result_json 取出本次领取标记。"""
    if isinstance(value, (bytes, str)):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    token = value.get("lease_token")
    if isinstance(token, str) and token:
        return token
    return None
