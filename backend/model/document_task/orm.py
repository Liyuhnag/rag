# -*- coding: utf-8 -*-
"""document_tasks 表的 SQL。"""

import json
from collections.abc import Iterator
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from uuid import uuid4

from model.common.database import as_utc
from model.common.database import bind_connection
from model.common.database import execute
from model.common.database import execute_count
from model.common.database import transaction
from model.document_task.lease import LeaseLost
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
    token = uuid4().hex
    count = execute_count(
        """
        UPDATE document_tasks
        SET status = %(status)s,
            stage = CASE WHEN stage IN ('CHUNKING', 'INDEXING') THEN stage ELSE %(stage)s END,
            progress = CASE WHEN stage = 'INDEXING' THEN progress ELSE 0 END,
            step = CASE WHEN stage = 'INDEXING' THEN step ELSE '解析中' END,
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
            "lease_token": token,
            "now": as_utc(now),
        },
    )
    if count != 1:
        return None
    task = get(task_id)
    # 更新和回读之间可能已被恢复并重新领取，不能把新 token 当作自己的。
    return task if task is not None and task.lease_token == token else None


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
    lease_token: str,
) -> bool:
    """仅更新本次领取的阶段、进度和步骤。"""
    _require_token(lease_token)
    changed = execute_count(
        """
        UPDATE document_tasks
        SET stage = %(stage)s,
            progress = %(progress)s,
            step = %(step)s,
            updated_at = %(now)s
        WHERE id = %(id)s AND status = 'RUNNING'
          AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.lease_token')) = %(lease_token)s
        """,
        {
            "id": task_id,
            "stage": stage.value,
            "progress": progress,
            "step": step,
            "now": as_utc(now),
            "lease_token": lease_token,
        },
    )
    return changed == 1 or owns_lease(task_id, lease_token)


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


def begin_merge(task_id: str, now: datetime, lease_token: str) -> bool:
    """只有一个调度能把解析阶段推进到合并。"""
    _require_token(lease_token)
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
          AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.lease_token')) = %(lease_token)s
        """,
        {
            "id": task_id,
            "stage": DocumentTaskStage.MERGING.value,
            "now": as_utc(now),
            "lease_token": lease_token,
        },
    )
    return count == 1


def complete(
    task_id: str,
    step: str,
    now: datetime,
    lease_token: str,
) -> bool:
    """把 RUNNING 任务推进到 COMPLETED/DONE。仅完成本次领取。"""
    _require_token(lease_token)
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
    lease_token: str,
) -> bool:
    """把 RUNNING 任务标记 FAILED。仅失败本次领取。"""
    _require_token(lease_token)
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
    sql += (
        " AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.lease_token'))"
        " = %(lease_token)s"
    )
    params["lease_token"] = lease_token
    return execute_count(sql, params) == 1


def _require_token(lease_token: str) -> None:
    if not lease_token:
        raise ValueError("任务缺少领取标记")


def owns_lease(
    task_id: str, lease_token: str, stages: Sequence[DocumentTaskStage] | None = None
) -> bool:
    """零行变化也可能只是心跳时间相同，重新确认执行权。"""
    _require_token(lease_token)
    rows = execute(
        "SELECT stage FROM document_tasks WHERE id = %(id)s AND status = 'RUNNING' "
        "AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.lease_token')) = %(lease_token)s",
        {"id": task_id, "lease_token": lease_token},
    )
    return bool(rows) and (stages is None or rows[0]["stage"] in stages)


def renew(
    task_id: str, lease_token: str, now: datetime, stages: Sequence[DocumentTaskStage]
) -> bool:
    """解析与切分共用的续租；阶段交接、恢复或换 token 后立即失效。"""
    _require_token(lease_token)
    holders = ", ".join(f"%(stage_{i})s" for i in range(len(stages)))
    params = {f"stage_{i}": stage for i, stage in enumerate(stages)}
    params.update(id=task_id, lease_token=lease_token, now=as_utc(now))
    changed = execute_count(
        "UPDATE document_tasks SET updated_at = %(now)s "
        "WHERE id = %(id)s AND status = 'RUNNING' "
        "AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.lease_token')) = %(lease_token)s "
        f"AND stage IN ({holders})",
        params,
    )
    return changed == 1 or owns_lease(task_id, lease_token, stages)


@contextmanager
def lease_guard(
    task_id: str, lease_token: str, stages: Sequence[DocumentTaskStage]
) -> Iterator[None]:
    """锁定执行权，将业务写入与执行权检查放在同一事务。"""
    _require_token(lease_token)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, stage, result_json FROM document_tasks "
                "WHERE id = %(id)s FOR UPDATE",
                {"id": task_id},
            )
            row = cursor.fetchone()
            if (
                row is None
                or row["status"] != "RUNNING"
                or row["stage"] not in stages
                or _lease_token(row["result_json"]) != lease_token
            ):
                raise LeaseLost(task_id)
            with bind_connection(connection):
                yield
            # 长事务期间恢复者在行锁外等待；提交前更新时间，避免刚提交就被恢复。
            cursor.execute(
                "UPDATE document_tasks SET updated_at = %(now)s "
                "WHERE id = %(id)s AND status = 'RUNNING' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.lease_token')) = %(lease_token)s",
                {
                    "id": task_id,
                    "lease_token": lease_token,
                    "now": as_utc(datetime.now(timezone.utc)),
                },
            )


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
