# -*- coding: utf-8 -*-
"""上传接口的进程内联调存储。

当前阶段只用于前后端联调：会话、分片进度和任务都保存在进程内，
不访问 MySQL 和对象存储。后续替换为真实实现时保持函数签名即可。
"""

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from threading import Lock
from uuid import uuid4


@dataclass
class UploadSession:
    """一次上传会话。"""

    id: str
    document_id: str
    fingerprint: str
    filename: str
    file_type: str
    size_bytes: int
    chunk_size: int
    total_parts: int
    object_key: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    status: str = "UPLOADING"
    abort_reason: str | None = None
    error_message: str | None = None
    completed_at: datetime | None = None
    uploaded_parts: dict[int, int] = field(default_factory=dict)
    task_id: str | None = None


@dataclass
class MockTask:
    """联调用的文档处理任务。"""

    id: str
    document_id: str
    status: str = "PENDING"
    stage: str = "PENDING"
    progress: int = 0
    step: str = "等待处理"
    error_message: str | None = None
    version: int = 1


_sessions: dict[str, UploadSession] = {}
_tasks: dict[str, MockTask] = {}
_lock = Lock()


def reset() -> None:
    """清空联调数据，测试之间互相隔离。"""
    with _lock:
        _sessions.clear()
        _tasks.clear()


def new_id() -> str:
    return uuid4().hex


def create_session(
    *,
    fingerprint: str,
    filename: str,
    file_type: str,
    size_bytes: int,
    chunk_size: int,
    total_parts: int,
    object_key: str,
    document_id: str,
    ttl_hours: int,
) -> UploadSession:
    now = datetime.now(timezone.utc)
    session = UploadSession(
        id=new_id(),
        document_id=document_id,
        fingerprint=fingerprint,
        filename=filename,
        file_type=file_type,
        size_bytes=size_bytes,
        chunk_size=chunk_size,
        total_parts=total_parts,
        object_key=object_key,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
    )
    with _lock:
        _sessions[session.id] = session
    return session


def get(upload_id: str) -> UploadSession | None:
    with _lock:
        return _sessions.get(upload_id)


def get_task(task_id: str | None) -> MockTask | None:
    if task_id is None:
        return None
    with _lock:
        return _tasks.get(task_id)


def _latest(fingerprint: str, status: str) -> UploadSession | None:
    candidates = [
        session
        for session in _sessions.values()
        if session.fingerprint == fingerprint and session.status == status
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda session: session.created_at)


def find_active(fingerprint: str, now: datetime) -> UploadSession | None:
    with _lock:
        active = _latest(fingerprint, "UPLOADING")
        if active is None or active.expires_at <= now:
            return None
        return active


def find_completing(fingerprint: str) -> UploadSession | None:
    with _lock:
        return _latest(fingerprint, "COMPLETING")


def find_completed(fingerprint: str) -> UploadSession | None:
    with _lock:
        return _latest(fingerprint, "COMPLETED")


def find_aborting(fingerprint: str) -> UploadSession | None:
    with _lock:
        return _latest(fingerprint, "ABORTING")


def touch(session: UploadSession, ttl_hours: int) -> None:
    now = datetime.now(timezone.utc)
    with _lock:
        session.updated_at = now
        session.expires_at = now + timedelta(hours=ttl_hours)


def add_part(session: UploadSession, part_number: int, size_bytes: int) -> None:
    with _lock:
        session.uploaded_parts[part_number] = size_bytes
        session.updated_at = datetime.now(timezone.utc)


def mark_completed(session: UploadSession) -> MockTask:
    now = datetime.now(timezone.utc)
    task = MockTask(
        id=new_id(),
        document_id=session.document_id,
        status="PENDING",
        stage="PENDING",
        progress=0,
        step="等待处理",
        version=1,
    )
    with _lock:
        _tasks[task.id] = task
        session.status = "COMPLETED"
        session.completed_at = now
        session.updated_at = now
        session.task_id = task.id
    return task


def mark_aborted(session: UploadSession, reason: str = "USER") -> None:
    now = datetime.now(timezone.utc)
    with _lock:
        if session.status in {"ABORTED", "EXPIRED"}:
            return
        session.status = "ABORTED"
        session.abort_reason = reason
        session.updated_at = now
