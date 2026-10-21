# -*- coding: utf-8 -*-
"""upload_sessions 表的 SQL。服务层只调用这里的函数。"""

from datetime import datetime

from model.common.database import as_aware
from model.common.database import as_utc
from model.common.database import execute
from model.common.database import execute_count
from model.common.database import transaction
from model.document.model import Document
from model.document_task.model import DocumentTask
from model.upload.model import UploadSession
from services.common.exceptions import StateConflict


def _from_row(row: dict[str, object]) -> UploadSession:
    return UploadSession(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        fingerprint=str(row["fingerprint"]),
        filename=str(row["filename"]),
        file_type=str(row["file_type"]),
        size_bytes=int(row["size_bytes"]),
        chunk_size=int(row["chunk_size"]),
        total_parts=int(row["total_parts"]),
        object_key=str(row["object_key"]),
        s3_upload_id=str(row["s3_upload_id"]),
        status=str(row["status"]),
        created_at=as_aware(row["created_at"]),
        updated_at=as_aware(row["updated_at"]),
        expires_at=as_aware(row["expires_at"]),
        abort_reason=(
            None if row["abort_reason"] is None else str(row["abort_reason"])
        ),
        claim_version=int(row["claim_version"]),
        error_message=(
            None if row["error_message"] is None else str(row["error_message"])
        ),
        completed_at=(
            None if row["completed_at"] is None else as_aware(row["completed_at"])
        ),
    )


def get(upload_id: str) -> UploadSession | None:
    rows = execute(
        "SELECT * FROM upload_sessions WHERE id = %(id)s",
        {"id": upload_id},
    )
    return None if not rows else _from_row(rows[0])


def find_active(fingerprint: str, now: datetime) -> UploadSession | None:
    rows = execute(
        """
        SELECT * FROM upload_sessions
        WHERE fingerprint = %(fingerprint)s
          AND status = 'UPLOADING'
          AND expires_at > %(now)s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"fingerprint": fingerprint, "now": as_utc(now)},
    )
    return None if not rows else _from_row(rows[0])


def find_latest(fingerprint: str, status: str) -> UploadSession | None:
    rows = execute(
        """
        SELECT * FROM upload_sessions
        WHERE fingerprint = %(fingerprint)s AND status = %(status)s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"fingerprint": fingerprint, "status": status},
    )
    return None if not rows else _from_row(rows[0])


def find_by_s3_upload_id(s3_upload_id: str) -> UploadSession | None:
    rows = execute(
        """
        SELECT * FROM upload_sessions
        WHERE s3_upload_id = %(s3_upload_id)s
        LIMIT 1
        """,
        {"s3_upload_id": s3_upload_id},
    )
    return None if not rows else _from_row(rows[0])


def find_expired_uploading(
    now: datetime,
    limit: int,
) -> list[UploadSession]:
    rows = execute(
        """
        SELECT * FROM upload_sessions
        WHERE status = 'UPLOADING' AND expires_at <= %(now)s
        ORDER BY expires_at ASC
        LIMIT %(limit)s
        """,
        {"now": as_utc(now), "limit": limit},
    )
    return [_from_row(row) for row in rows]


def find_aborting_before(
    older_than: datetime,
    limit: int,
) -> list[UploadSession]:
    rows = execute(
        """
        SELECT * FROM upload_sessions
        WHERE status = 'ABORTING' AND updated_at < %(older_than)s
        ORDER BY updated_at ASC
        LIMIT %(limit)s
        """,
        {"older_than": as_utc(older_than), "limit": limit},
    )
    return [_from_row(row) for row in rows]


def find_completing_before(
    older_than: datetime,
    limit: int,
) -> list[UploadSession]:
    rows = execute(
        """
        SELECT * FROM upload_sessions
        WHERE status = 'COMPLETING' AND updated_at < %(older_than)s
        ORDER BY updated_at ASC
        LIMIT %(limit)s
        """,
        {"older_than": as_utc(older_than), "limit": limit},
    )
    return [_from_row(row) for row in rows]


def find_terminal_before(
    older_than: datetime,
    limit: int,
) -> list[UploadSession]:
    rows = execute(
        """
        SELECT * FROM upload_sessions
        WHERE status IN ('FAILED', 'ABORTED', 'EXPIRED')
          AND updated_at < %(older_than)s
        ORDER BY updated_at ASC
        LIMIT %(limit)s
        """,
        {"older_than": as_utc(older_than), "limit": limit},
    )
    return [_from_row(row) for row in rows]


def delete_old_sessions(
    older_than: datetime,
    limit: int,
) -> int:
    return execute_count(
        """
        DELETE FROM upload_sessions
        WHERE status IN ('COMPLETED', 'FAILED', 'ABORTED', 'EXPIRED')
          AND updated_at < %(older_than)s
        ORDER BY updated_at ASC
        LIMIT %(limit)s
        """,
        {"older_than": as_utc(older_than), "limit": limit},
    )


def insert(session: UploadSession) -> None:
    execute(
        """
        INSERT INTO upload_sessions (
            id, document_id, fingerprint, filename, file_type,
            size_bytes, chunk_size, total_parts, object_key,
            s3_upload_id, status, abort_reason, claim_version,
            error_message, created_at, updated_at, expires_at,
            completed_at
        ) VALUES (
            %(id)s, %(document_id)s, %(fingerprint)s, %(filename)s,
            %(file_type)s, %(size_bytes)s, %(chunk_size)s,
            %(total_parts)s, %(object_key)s, %(s3_upload_id)s,
            %(status)s, %(abort_reason)s, %(claim_version)s,
            %(error_message)s, %(created_at)s, %(updated_at)s,
            %(expires_at)s, %(completed_at)s
        )
        """,
        {
            "id": session.id,
            "document_id": session.document_id,
            "fingerprint": session.fingerprint,
            "filename": session.filename,
            "file_type": session.file_type,
            "size_bytes": session.size_bytes,
            "chunk_size": session.chunk_size,
            "total_parts": session.total_parts,
            "object_key": session.object_key,
            "s3_upload_id": session.s3_upload_id,
            "status": session.status,
            "abort_reason": session.abort_reason,
            "claim_version": session.claim_version,
            "error_message": session.error_message,
            "created_at": as_utc(session.created_at),
            "updated_at": as_utc(session.updated_at),
            "expires_at": as_utc(session.expires_at),
            "completed_at": (
                None
                if session.completed_at is None
                else as_utc(session.completed_at)
            ),
        },
    )


def touch(upload_id: str, expires_at: datetime, updated_at: datetime) -> None:
    execute(
        """
        UPDATE upload_sessions
        SET expires_at = %(expires_at)s,
            updated_at = %(updated_at)s
        WHERE id = %(id)s
        """,
        {
            "id": upload_id,
            "expires_at": as_utc(expires_at),
            "updated_at": as_utc(updated_at),
        },
    )


def claim_completing(upload_id: str, now: datetime) -> bool:
    count = execute_count(
        """
        UPDATE upload_sessions
        SET status = 'COMPLETING',
            claim_version = claim_version + 1,
            updated_at = %(now)s
        WHERE id = %(id)s
          AND status = 'UPLOADING'
          AND expires_at > %(now)s
        """,
        {"id": upload_id, "now": as_utc(now)},
    )
    return count == 1


def revert_to_uploading(
    upload_id: str,
    claim_version: int,
    now: datetime,
) -> None:
    execute_count(
        """
        UPDATE upload_sessions
        SET status = 'UPLOADING',
            updated_at = %(now)s
        WHERE id = %(id)s
          AND status = 'COMPLETING'
          AND claim_version = %(claim_version)s
        """,
        {
            "id": upload_id,
            "claim_version": claim_version,
            "now": as_utc(now),
        },
    )


def mark_aborting(
    upload_id: str,
    reason: str,
    updated_at: datetime,
) -> bool:
    count = execute_count(
        """
        UPDATE upload_sessions
        SET status = 'ABORTING',
            abort_reason = %(reason)s,
            updated_at = %(updated_at)s
        WHERE id = %(id)s
          AND status = 'UPLOADING'
        """,
        {
            "id": upload_id,
            "reason": reason,
            "updated_at": as_utc(updated_at),
        },
    )
    return count == 1


def mark_aborting_completing(
    upload_id: str,
    reason: str,
    updated_at: datetime,
) -> bool:
    count = execute_count(
        """
        UPDATE upload_sessions
        SET status = 'ABORTING',
            abort_reason = %(reason)s,
            updated_at = %(updated_at)s
        WHERE id = %(id)s
          AND status = 'COMPLETING'
        """,
        {
            "id": upload_id,
            "reason": reason,
            "updated_at": as_utc(updated_at),
        },
    )
    return count == 1


def finish_abort(upload_id: str, status: str, updated_at: datetime) -> None:
    execute(
        """
        UPDATE upload_sessions
        SET status = %(status)s,
            updated_at = %(updated_at)s
        WHERE id = %(id)s
          AND status = 'ABORTING'
        """,
        {
            "id": upload_id,
            "status": status,
            "updated_at": as_utc(updated_at),
        },
    )


def mark_failed(upload_id: str, message: str, updated_at: datetime) -> None:
    execute(
        """
        UPDATE upload_sessions
        SET status = 'FAILED',
            error_message = %(message)s,
            updated_at = %(updated_at)s
        WHERE id = %(id)s
        """,
        {
            "id": upload_id,
            "message": message[:1024],
            "updated_at": as_utc(updated_at),
        },
    )


def complete_session(
    upload_id: str,
    claim_version: int,
    document: Document,
    task: DocumentTask,
    now: datetime,
) -> None:
    """在同一个事务中写文档、写任务并将会话置为 COMPLETED。"""
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO documents (
                    id, name, file_type, size_bytes, chunk_count,
                    status, object_key, file_url, previewable,
                    preview_url, uploaded_at, updated_at
                ) VALUES (
                    %(id)s, %(name)s, %(file_type)s, %(size_bytes)s,
                    %(chunk_count)s, %(status)s, %(object_key)s,
                    %(file_url)s, %(previewable)s, %(preview_url)s,
                    %(uploaded_at)s, %(updated_at)s
                )
                """,
                {
                    "id": document.id,
                    "name": document.name,
                    "file_type": document.file_type,
                    "size_bytes": document.size_bytes,
                    "chunk_count": document.chunk_count,
                    "status": document.status.value,
                    "object_key": document.object_key,
                    "file_url": document.file_url,
                    "previewable": int(document.previewable),
                    "preview_url": document.preview_url,
                    "uploaded_at": as_utc(document.uploaded_at),
                    "updated_at": as_utc(document.updated_at),
                },
            )
            cursor.execute(
                """
                INSERT INTO document_tasks (
                    id, document_id, version, status, stage,
                    progress, step, error_message, result_json,
                    created_at, updated_at
                ) VALUES (
                    %(id)s, %(document_id)s, %(version)s, %(status)s,
                    %(stage)s, %(progress)s, %(step)s, %(error_message)s,
                    NULL, %(created_at)s, %(updated_at)s
                )
                """,
                {
                    "id": task.id,
                    "document_id": task.document_id,
                    "version": task.version,
                    "status": task.status,
                    "stage": task.stage,
                    "progress": task.progress,
                    "step": task.step,
                    "error_message": task.error_message,
                    "created_at": as_utc(now),
                    "updated_at": as_utc(now),
                },
            )
            cursor.execute(
                """
                UPDATE upload_sessions
                SET status = 'COMPLETED',
                    completed_at = %(now)s,
                    updated_at = %(now)s
                WHERE id = %(id)s
                  AND status = 'COMPLETING'
                  AND claim_version = %(claim_version)s
                """,
                {
                    "id": upload_id,
                    "claim_version": claim_version,
                    "now": as_utc(now),
                },
            )
            if cursor.rowcount != 1:
                raise StateConflict("上传会话状态已变化")
