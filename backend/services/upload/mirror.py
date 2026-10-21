# -*- coding: utf-8 -*-
"""把真实上传会话镜像到联调存储。

这是接口逐个替换期间的过渡层：init 已经访问 MySQL/S3，而 part/get/complete
仍是进程内桩，通过镜像让前端继续联调。后续接口替换完成后删除本模块。
"""

from model.document_task.model import DocumentTask
from model.upload.model import UploadSession as UploadSessionRecord
from services.common.object_storage import UploadedPart
from services.upload.store import MockTask
from services.upload.store import UploadSession as StoredUploadSession
from services.upload.store import _lock
from services.upload.store import _sessions
from services.upload.store import _tasks


def mirror(
    record: UploadSessionRecord,
    uploaded_parts: list[UploadedPart],
    task: DocumentTask | None = None,
) -> None:
    with _lock:
        session = _sessions.get(record.id)
        if session is None:
            session = StoredUploadSession(
                id=record.id,
                document_id=record.document_id,
                fingerprint=record.fingerprint,
                filename=record.filename,
                file_type=record.file_type,
                size_bytes=record.size_bytes,
                chunk_size=record.chunk_size,
                total_parts=record.total_parts,
                object_key=record.object_key,
                created_at=record.created_at,
                updated_at=record.updated_at,
                expires_at=record.expires_at,
            )
            _sessions[record.id] = session

        session.status = record.status
        session.abort_reason = record.abort_reason
        session.error_message = record.error_message
        session.completed_at = record.completed_at
        session.expires_at = record.expires_at
        session.uploaded_parts = {
            part.part_number: part.size_bytes for part in uploaded_parts
        }

        if task is not None:
            session.task_id = task.id
            _tasks[task.id] = MockTask(
                id=task.id,
                document_id=task.document_id,
                status=task.status,
                stage=task.stage,
                progress=task.progress,
                step=task.step,
                error_message=task.error_message,
                version=task.version,
            )
