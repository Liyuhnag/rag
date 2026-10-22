# -*- coding: utf-8 -*-
"""测试用的进程内文档、上传和任务存储。"""

from datetime import datetime
from datetime import timezone
from hashlib import md5
from uuid import uuid4

import pytest

from config import Settings
from model.document import Document
from model.document import orm as document_orm
from services.common.exceptions import DuplicateRecord
from services.common.exceptions import StateConflict
from services.common.exceptions import UploadNotFound
from services.common.object_storage import MultipartUploadInfo
from services.common.object_storage import ObjectStorage
from services.common.object_storage import UploadedPart
from services.common.object_storage import configure
from main import celery_app
from model.document_task import orm as document_task_orm
from model.document_task.model import DocumentTask
from model.upload import orm as upload_orm
from model.upload.model import UploadSession


class MemoryObjectStorage(ObjectStorage):
    """进程内对象存储，支持 Multipart Upload。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.multipart: dict[str, dict[str, object]] = {}

    def put(self, key: str, content: bytes, content_type: str) -> None:
        del content_type
        self.objects[key] = content

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def create_multipart_upload(self, key: str, content_type: str) -> str:
        del content_type
        upload_id = uuid4().hex
        self.multipart[upload_id] = {
            "key": key,
            "parts": {},
            "initiated": datetime.now(timezone.utc),
        }
        return upload_id

    def upload_part(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        body: bytes,
    ) -> str:
        upload = self.multipart.get(upload_id)
        if upload is None or upload["key"] != key:
            raise UploadNotFound("分片上传不存在")
        etag = f'"{md5(body).hexdigest()}"'
        parts = upload["parts"]
        assert isinstance(parts, dict)
        parts[part_number] = {"etag": etag, "size": len(body), "body": body}
        return etag

    def list_parts(self, key: str, upload_id: str) -> list[UploadedPart]:
        upload = self.multipart.get(upload_id)
        if upload is None or upload["key"] != key:
            raise UploadNotFound("分片上传不存在")
        parts = upload["parts"]
        assert isinstance(parts, dict)
        return [
            UploadedPart(
                part_number=number,
                etag=str(part["etag"]),
                size_bytes=int(part["size"]),
            )
            for number, part in sorted(parts.items())
        ]

    def complete_multipart_upload(
        self,
        key: str,
        upload_id: str,
        parts: list[dict[str, object]],
    ) -> None:
        upload = self.multipart.get(upload_id)
        if upload is None or upload["key"] != key:
            raise UploadNotFound("分片上传不存在")
        stored = upload["parts"]
        assert isinstance(stored, dict)
        ordered = [stored[int(part["PartNumber"])] for part in parts]
        self.objects[key] = b"".join(
            bytes(part["body"]) for part in ordered
        )
        self.multipart.pop(upload_id, None)

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        upload = self.multipart.get(upload_id)
        if upload is not None and upload["key"] == key:
            self.multipart.pop(upload_id, None)

    def head_object(self, key: str) -> int | None:
        content = self.objects.get(key)
        return None if content is None else len(content)

    def list_multipart_uploads(
        self,
        prefix: str,
    ) -> list[MultipartUploadInfo]:
        return [
            MultipartUploadInfo(
                key=str(upload["key"]),
                upload_id=upload_id,
                initiated=upload["initiated"],
            )
            for upload_id, upload in self.multipart.items()
            if str(upload["key"]).startswith(prefix)
        ]


class MemoryDocumentRepository:
    """进程内文档记录。"""

    def __init__(self) -> None:
        self.records: dict[str, Document] = {}
        self.document_ids: set[str] = set()

    def insert(self, document: Document) -> None:
        self.records[document.id] = document
        self.document_ids.add(document.id)

    def mark_exists(self, document_id: str) -> None:
        self.document_ids.add(document_id)

    def exists(self, document_id: str) -> bool:
        return document_id in self.document_ids or document_id in self.records

    def get(self, document_id: str) -> Document | None:
        return self.records.get(document_id)

    def delete(self, document_id: str) -> Document | None:
        return self.records.pop(document_id, None)

    def list_page(
        self,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[Document], int]:
        records = list(self.records.values())
        reverse = sort_order != "asc"

        def sort_key(record: Document) -> tuple[object, str]:
            if sort_by == "name":
                value: object = record.name
            elif sort_by == "size_bytes":
                value = record.size_bytes
            else:
                value = record.uploaded_at
            return (value, record.id)

        records.sort(key=sort_key, reverse=reverse)
        start = (page - 1) * page_size
        return records[start : start + page_size], len(records)


def use_memory_records(
    monkeypatch: pytest.MonkeyPatch,
    records: MemoryDocumentRepository,
) -> None:
    """让文档服务的数据库调用走进程内记录。"""
    monkeypatch.setattr(document_orm, "insert", records.insert)
    monkeypatch.setattr(document_orm, "get", records.get)
    monkeypatch.setattr(document_orm, "delete", records.delete)
    monkeypatch.setattr(document_orm, "list_page", records.list_page)
    monkeypatch.setattr(document_orm, "exists", records.exists)


class MemoryUploadRepository:
    """进程内上传会话和文档任务。"""

    def __init__(self) -> None:
        self.sessions: dict[str, UploadSession] = {}
        self.tasks: dict[tuple[str, int], DocumentTask] = {}

    def find_active(
        self,
        fingerprint: str,
        now: datetime,
    ) -> UploadSession | None:
        candidates = [
            session
            for session in self.sessions.values()
            if session.fingerprint == fingerprint
            and session.status == "UPLOADING"
            and session.expires_at > now
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda session: session.created_at)

    def find_latest(
        self,
        fingerprint: str,
        status: str,
    ) -> UploadSession | None:
        candidates = [
            session
            for session in self.sessions.values()
            if session.fingerprint == fingerprint and session.status == status
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda session: session.created_at)

    def get(self, upload_id: str) -> UploadSession | None:
        return self.sessions.get(upload_id)

    def claim_completing(self, upload_id: str, now: datetime) -> bool:
        session = self.sessions.get(upload_id)
        if (
            session is None
            or session.status != "UPLOADING"
            or session.expires_at <= now
        ):
            return False
        session.status = "COMPLETING"
        session.claim_version += 1
        session.updated_at = now
        return True

    def revert_to_uploading(
        self,
        upload_id: str,
        claim_version: int,
        now: datetime,
    ) -> None:
        session = self.sessions.get(upload_id)
        if (
            session is None
            or session.status != "COMPLETING"
            or session.claim_version != claim_version
        ):
            return
        session.status = "UPLOADING"
        session.updated_at = now

    def finish_abort(self, upload_id: str, status: str, now: datetime) -> None:
        session = self.sessions.get(upload_id)
        if session is None or session.status != "ABORTING":
            return
        session.status = status
        session.updated_at = now

    def complete_session(
        self,
        upload_id: str,
        claim_version: int,
        document,
        task,
        now: datetime,
    ) -> None:
        session = self.sessions.get(upload_id)
        if (
            session is None
            or session.status != "COMPLETING"
            or session.claim_version != claim_version
        ):
            raise StateConflict("上传会话状态已变化")
        session.status = "COMPLETED"
        session.completed_at = now
        session.updated_at = now
        self.tasks[(task.document_id, task.version)] = task

    def get_task(self, task_id: str):
        for task in self.tasks.values():
            if task.id == task_id:
                return task
        return None

    def insert(self, session: UploadSession) -> None:
        for existing in self.sessions.values():
            if existing.id == session.id:
                raise DuplicateRecord("duplicate upload id")
            if (
                existing.fingerprint == session.fingerprint
                and existing.status in {"UPLOADING", "COMPLETING"}
                and session.status in {"UPLOADING", "COMPLETING"}
            ):
                raise DuplicateRecord("duplicate active fingerprint")
        self.sessions[session.id] = session

    def touch(
        self,
        upload_id: str,
        expires_at: datetime,
        updated_at: datetime,
    ) -> None:
        session = self.sessions[upload_id]
        session.expires_at = expires_at
        session.updated_at = updated_at

    def mark_aborting(
        self,
        upload_id: str,
        reason: str,
        updated_at: datetime,
    ) -> bool:
        session = self.sessions[upload_id]
        if session.status != "UPLOADING":
            return False
        session.status = "ABORTING"
        session.abort_reason = reason
        session.updated_at = updated_at
        return True

    def mark_aborting_completing(
        self,
        upload_id: str,
        reason: str,
        updated_at: datetime,
    ) -> bool:
        session = self.sessions[upload_id]
        if session.status != "COMPLETING":
            return False
        session.status = "ABORTING"
        session.abort_reason = reason
        session.updated_at = updated_at
        return True

    def mark_failed(
        self,
        upload_id: str,
        message: str,
        updated_at: datetime,
    ) -> None:
        session = self.sessions[upload_id]
        session.status = "FAILED"
        session.error_message = message[:1024]
        session.updated_at = updated_at

    def find_by_s3_upload_id(self, s3_upload_id: str) -> UploadSession | None:
        for session in self.sessions.values():
            if session.s3_upload_id == s3_upload_id:
                return session
        return None

    def find_expired_uploading(
        self,
        now: datetime,
        limit: int,
    ) -> list[UploadSession]:
        sessions = [
            session
            for session in self.sessions.values()
            if session.status == "UPLOADING" and session.expires_at <= now
        ]
        sessions.sort(key=lambda session: session.expires_at)
        return sessions[:limit]

    def find_aborting_before(
        self,
        older_than: datetime,
        limit: int,
    ) -> list[UploadSession]:
        sessions = [
            session
            for session in self.sessions.values()
            if session.status == "ABORTING" and session.updated_at < older_than
        ]
        sessions.sort(key=lambda session: session.updated_at)
        return sessions[:limit]

    def find_completing_before(
        self,
        older_than: datetime,
        limit: int,
    ) -> list[UploadSession]:
        sessions = [
            session
            for session in self.sessions.values()
            if session.status == "COMPLETING" and session.updated_at < older_than
        ]
        sessions.sort(key=lambda session: session.updated_at)
        return sessions[:limit]

    def find_terminal_before(
        self,
        older_than: datetime,
        limit: int,
    ) -> list[UploadSession]:
        sessions = [
            session
            for session in self.sessions.values()
            if session.status in {"FAILED", "ABORTED", "EXPIRED"}
            and session.updated_at < older_than
        ]
        sessions.sort(key=lambda session: session.updated_at)
        return sessions[:limit]

    def delete_old_sessions(self, older_than: datetime, limit: int) -> int:
        sessions = [
            session
            for session in self.sessions.values()
            if session.status in {"COMPLETED", "FAILED", "ABORTED", "EXPIRED"}
            and session.updated_at < older_than
        ]
        sessions.sort(key=lambda session: session.updated_at)
        for session in sessions[:limit]:
            self.sessions.pop(session.id, None)
        return min(len(sessions), limit)

    def find_task(
        self,
        document_id: str,
        version: int,
    ) -> DocumentTask | None:
        return self.tasks.get((document_id, version))

    def add_task(self, task: DocumentTask) -> None:
        self.tasks[(task.document_id, task.version)] = task


def use_memory_upload_repository(
    monkeypatch: pytest.MonkeyPatch,
    repository: MemoryUploadRepository,
) -> None:
    """让上传服务走进程内会话和任务。"""
    monkeypatch.setattr(upload_orm, "get", repository.get)
    monkeypatch.setattr(upload_orm, "find_active", repository.find_active)
    monkeypatch.setattr(upload_orm, "find_latest", repository.find_latest)
    monkeypatch.setattr(upload_orm, "insert", repository.insert)
    monkeypatch.setattr(upload_orm, "touch", repository.touch)
    monkeypatch.setattr(
        upload_orm,
        "claim_completing",
        repository.claim_completing,
    )
    monkeypatch.setattr(
        upload_orm,
        "revert_to_uploading",
        repository.revert_to_uploading,
    )
    monkeypatch.setattr(upload_orm, "mark_aborting", repository.mark_aborting)
    monkeypatch.setattr(
        upload_orm,
        "mark_aborting_completing",
        repository.mark_aborting_completing,
    )
    monkeypatch.setattr(upload_orm, "finish_abort", repository.finish_abort)
    monkeypatch.setattr(upload_orm, "mark_failed", repository.mark_failed)
    monkeypatch.setattr(
        upload_orm,
        "find_by_s3_upload_id",
        repository.find_by_s3_upload_id,
    )
    monkeypatch.setattr(
        upload_orm,
        "find_expired_uploading",
        repository.find_expired_uploading,
    )
    monkeypatch.setattr(
        upload_orm,
        "find_aborting_before",
        repository.find_aborting_before,
    )
    monkeypatch.setattr(
        upload_orm,
        "find_completing_before",
        repository.find_completing_before,
    )
    monkeypatch.setattr(
        upload_orm,
        "find_terminal_before",
        repository.find_terminal_before,
    )
    monkeypatch.setattr(
        upload_orm,
        "delete_old_sessions",
        repository.delete_old_sessions,
    )
    monkeypatch.setattr(
        upload_orm,
        "complete_session",
        repository.complete_session,
    )
    monkeypatch.setattr(document_task_orm, "find", repository.find_task)
    monkeypatch.setattr(document_task_orm, "get", repository.get_task)


def use_inline_tasks() -> None:
    """在请求里把任务跑完，不连接 Redis。"""
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True


def open_memory_storage(settings: Settings) -> None:
    """接入进程内对象存储。"""
    configure(
        MemoryObjectStorage(),
        bucket=settings.s3_bucket,
        file_base_url=settings.file_base_url,
    )
