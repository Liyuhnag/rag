# -*- coding: utf-8 -*-
"""parse_jobs 表的 SQL。"""

from datetime import datetime
from datetime import timedelta

from model.common.database import as_aware
from model.common.database import as_utc
from model.common.database import execute
from model.common.database import execute_count
from model.common.database import transaction
from model.parse_job.model import ParseJob
from model.parse_job.model import ParseJobStatus


def _from_row(row: dict[str, object]) -> ParseJob:
    """把一行 parse_jobs 记录转换为模型。"""
    return ParseJob(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        task_id=str(row["task_id"]),
        version=int(row["version"]),
        lease_token=row.get("lease_token"),
        part_index=int(row["part_index"]),
        page_start=int(row["page_start"]),
        page_end=int(row["page_end"]),
        source_key=str(row["source_key"]),
        mineru_file_id=(
            None
            if row["mineru_file_id"] is None
            else str(row["mineru_file_id"])
        ),
        mineru_job_id=(
            None
            if row["mineru_job_id"] is None
            else str(row["mineru_job_id"])
        ),
        status=ParseJobStatus(str(row["status"])),
        attempts=int(row["attempts"]),
        next_poll_at=(
            None
            if row["next_poll_at"] is None
            else as_aware(row["next_poll_at"])
        ),
        error_message=(
            None
            if row["error_message"] is None
            else str(row["error_message"])
        ),
        result_key=(
            None if row["result_key"] is None else str(row["result_key"])
        ),
        created_at=as_aware(row["created_at"]),
        updated_at=as_aware(row["updated_at"]),
    )


def insert(job: ParseJob) -> None:
    """写入一条 PDF 分段解析任务，保留提交者的执行标记。"""
    if not job.lease_token:
        raise ValueError("分段任务缺少领取标记")
    execute(
        """
        INSERT INTO parse_jobs (
            id, document_id, task_id, version, lease_token, part_index,
            page_start, page_end, source_key, mineru_file_id,
            mineru_job_id, status, attempts, next_poll_at,
            error_message, result_key, created_at, updated_at
        ) VALUES (
            %(id)s, %(document_id)s, %(task_id)s, %(version)s, %(lease_token)s,
            %(part_index)s, %(page_start)s, %(page_end)s,
            %(source_key)s, %(mineru_file_id)s, %(mineru_job_id)s,
            %(status)s, %(attempts)s, %(next_poll_at)s,
            %(error_message)s, %(result_key)s, %(created_at)s,
            %(updated_at)s
        )
        """,
        {
            "id": job.id,
            "document_id": job.document_id,
            "task_id": job.task_id,
            "version": job.version,
            "lease_token": job.lease_token,
            "part_index": job.part_index,
            "page_start": job.page_start,
            "page_end": job.page_end,
            "source_key": job.source_key,
            "mineru_file_id": job.mineru_file_id,
            "mineru_job_id": job.mineru_job_id,
            "status": job.status.value,
            "attempts": job.attempts,
            "next_poll_at": (
                None
                if job.next_poll_at is None
                else as_utc(job.next_poll_at)
            ),
            "error_message": job.error_message,
            "result_key": job.result_key,
            "created_at": as_utc(job.created_at),
            "updated_at": as_utc(job.updated_at),
        },
    )


def find_part(document_id: str, version: int, part_index: int) -> ParseJob | None:
    """按文档、版本和段号取最近一条分段，供重跑时跳过已提交的 MinerU 任务。"""
    rows = execute(
        """
        SELECT * FROM parse_jobs
        WHERE document_id = %(document_id)s AND version = %(version)s
          AND part_index = %(part_index)s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        {
            "document_id": document_id,
            "version": version,
            "part_index": part_index,
        },
    )
    return None if not rows else _from_row(rows[0])


def get(job_id: str) -> ParseJob | None:
    """按 ID 查询分段任务；不存在返回 None。"""
    rows = execute(
        "SELECT * FROM parse_jobs WHERE id = %(id)s LIMIT 1",
        {"id": job_id},
    )
    return None if not rows else _from_row(rows[0])


def list_by_document_version(
    document_id: str,
    version: int,
    lease_token: str,
) -> list[ParseJob]:
    """按文档、版本和领取批次查询分段，按 part_index 升序返回。"""
    rows = execute(
        """
        SELECT * FROM parse_jobs
        WHERE document_id = %(document_id)s AND version = %(version)s
          AND lease_token = %(lease_token)s
        ORDER BY part_index ASC
        """,
        {"document_id": document_id, "version": version, "lease_token": lease_token},
    )
    return [_from_row(row) for row in rows]


def set_status(
    job_id: str,
    status: ParseJobStatus,
    now: datetime,
    next_poll_at: datetime | None = None,
    error_message: str | None = None,
) -> None:
    """更新分段任务状态、下次轮询时间和失败原因。"""
    execute_count(
        """
        UPDATE parse_jobs
        SET status = %(status)s,
            next_poll_at = %(next_poll_at)s,
            error_message = %(error_message)s,
            updated_at = %(now)s
        WHERE id = %(id)s
        """,
        {
            "id": job_id,
            "status": status.value,
            "next_poll_at": (
                None if next_poll_at is None else as_utc(next_poll_at)
            ),
            "error_message": error_message,
            "now": as_utc(now),
        },
    )


def claim_due(now: datetime, limit: int) -> list[ParseJob]:
    """领取到期的分段，并把下次轮询时间往后推，避免两个调度同时查。"""
    if limit <= 0:
        return []
    poll_lease = now + timedelta(seconds=10)
    download_lease = now + timedelta(seconds=300)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM parse_jobs
                WHERE EXISTS (
                    SELECT 1 FROM document_tasks AS t
                    WHERE t.id = parse_jobs.task_id AND t.status = 'RUNNING'
                      AND t.stage = 'PARSING'
                      AND JSON_UNQUOTE(JSON_EXTRACT(t.result_json, '$.lease_token'))
                          = parse_jobs.lease_token
                ) AND next_poll_at IS NOT NULL
                  AND next_poll_at <= %(now)s
                  AND (
                    status IN ('SUBMITTED', 'RUNNING', 'RESUBMITTING')
                    OR (status = 'DOWNLOADING' AND result_key IS NULL)
                  )
                ORDER BY next_poll_at
                LIMIT %(limit)s
                FOR UPDATE SKIP LOCKED
                """,
                {"now": as_utc(now), "limit": limit},
            )
            rows = list(cursor.fetchall())
            if not rows:
                return []
            holders = ", ".join(f"%(id_{index})s" for index in range(len(rows)))
            params: dict[str, object] = {
                f"id_{index}": row["id"] for index, row in enumerate(rows)
            }
            params["poll_lease"] = as_utc(poll_lease)
            params["download_lease"] = as_utc(download_lease)
            params["now"] = as_utc(now)
            cursor.execute(
                f"""
                UPDATE parse_jobs
                SET next_poll_at = CASE
                        WHEN status IN ('DOWNLOADING', 'RESUBMITTING') THEN %(download_lease)s
                        ELSE %(poll_lease)s
                    END,
                    updated_at = %(now)s
                WHERE id IN ({holders})
                """,
                params,
            )
    return [_from_row(row) for row in rows]


def mark_retry(
    job_id: str,
    next_poll_at: datetime,
    error_message: str | None,
    now: datetime,
) -> None:
    """安排重新提交。轮询先跳过这段，等提交任务写回新的 job id。"""
    execute_count(
        """
        UPDATE parse_jobs
        SET status = %(status)s,
            next_poll_at = %(next_poll_at)s,
            error_message = %(error_message)s,
            updated_at = %(now)s
        WHERE id = %(id)s
        """,
        {
            "id": job_id,
            "status": ParseJobStatus.RESUBMITTING.value,
            "next_poll_at": as_utc(next_poll_at),
            "error_message": error_message,
            "now": as_utc(now),
        },
    )


def record_submission(
    job_id: str,
    previous_job_id: str | None,
    file_id: str,
    mineru_job_id: str,
    next_poll_at: datetime,
    now: datetime,
) -> bool:
    """重新提交成功后写回 MinerU 编号。旧编号已被替换时不再加次数。"""
    count = execute_count(
        """
        UPDATE parse_jobs
        SET mineru_file_id = %(file_id)s,
            mineru_job_id = %(mineru_job_id)s,
            status = %(status)s,
            attempts = attempts + 1,
            next_poll_at = %(next_poll_at)s,
            error_message = NULL,
            updated_at = %(now)s
        WHERE id = %(id)s
          AND mineru_job_id <=> %(previous_job_id)s
        """,
        {
            "id": job_id,
            "previous_job_id": previous_job_id,
            "file_id": file_id,
            "mineru_job_id": mineru_job_id,
            "status": ParseJobStatus.SUBMITTED.value,
            "next_poll_at": as_utc(next_poll_at),
            "now": as_utc(now),
        },
    )
    return count == 1


def save_zip(job_id: str, zip_key: str, now: datetime) -> bool:
    """记下已下载的压缩包。只有第一次写入成功。"""
    count = execute_count(
        """
        UPDATE parse_jobs
        SET result_key = %(zip_key)s,
            updated_at = %(now)s
        WHERE id = %(id)s
          AND status = 'DOWNLOADING'
          AND result_key IS NULL
        """,
        {
            "id": job_id,
            "zip_key": zip_key,
            "now": as_utc(now),
        },
    )
    return count == 1


def record_result(job_id: str, result_key: str, now: datetime) -> None:
    """记录分段结果 key，并将会话推进到 COMPLETED。"""
    execute_count(
        """
        UPDATE parse_jobs
        SET status = %(status)s,
            result_key = %(result_key)s,
            next_poll_at = NULL,
            updated_at = %(now)s
        WHERE id = %(id)s
        """,
        {
            "id": job_id,
            "status": ParseJobStatus.COMPLETED.value,
            "result_key": result_key,
            "now": as_utc(now),
        },
    )


def adopt_task_jobs(task_id: str, document_id: str, version: int, token: str) -> list[ParseJob]:
    """在任务租约事务内接管最近一组可靠分段，保留远端编号与 ZIP。

    调用方必须先持有任务行门禁；不能将升级前 token 为空的分段认领为当前结果。
    """
    old_token = _latest_task_token(task_id, document_id, version)
    if old_token is None:
        return []
    execute_count(
        "UPDATE parse_jobs SET lease_token=%(token)s WHERE task_id=%(task_id)s "
        "AND document_id=%(document_id)s AND version=%(version)s AND lease_token=%(old)s",
        {"task_id": task_id, "document_id": document_id, "version": version,
         "token": token, "old": old_token},
    )
    return list_by_document_version(document_id, version, token)


def retry_failed_parts(task_id: str, document_id: str, version: int, now: datetime) -> None:
    """在 retry 事务内准备最近一组分段；仅失败且无 ZIP 的分段需要重新提交。"""
    token = _latest_task_token(task_id, document_id, version)
    if token is None:
        return
    execute_count(
        """
        UPDATE parse_jobs
        SET status = CASE WHEN result_key IS NULL THEN 'SUBMITTED' ELSE 'COMPLETED' END,
            mineru_file_id = CASE WHEN result_key IS NULL THEN NULL ELSE mineru_file_id END,
            mineru_job_id = CASE WHEN result_key IS NULL THEN NULL ELSE mineru_job_id END,
            attempts = CASE WHEN result_key IS NULL THEN 0 ELSE attempts END,
            next_poll_at = CASE WHEN result_key IS NULL THEN %(now)s ELSE NULL END,
            error_message = NULL, updated_at = %(now)s
        WHERE task_id = %(task_id)s AND document_id = %(document_id)s AND version = %(version)s
          AND lease_token = %(token)s AND status = 'FAILED'
        """,
        {"task_id": task_id, "document_id": document_id, "version": version,
         "token": token, "now": as_utc(now)},
    )


def _latest_task_token(task_id: str, document_id: str, version: int) -> str | None:
    """恢复和手动重试使用同一分段组，跳过升级前没有 token 的历史记录。"""
    rows = execute(
        "SELECT lease_token FROM parse_jobs WHERE task_id=%(task_id)s "
        "AND document_id=%(document_id)s AND version=%(version)s "
        "AND lease_token IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 1",
        {"task_id": task_id, "document_id": document_id, "version": version},
    )
    return None if not rows else str(rows[0]["lease_token"])
