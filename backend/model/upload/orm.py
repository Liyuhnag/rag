# -*- coding: utf-8 -*-
"""upload_sessions 表的 SQL。服务层只调用这里的函数。"""

from datetime import datetime

from model.common.database import as_aware
from model.common.database import as_utc
from model.common.database import execute
from model.upload.model import UploadSession


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


def mark_aborting(
    upload_id: str,
    reason: str,
    updated_at: datetime,
) -> None:
    execute(
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
