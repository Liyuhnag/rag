# -*- coding: utf-8 -*-
"""大文件上传会话模型，对应 upload_sessions 表的一行。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class UploadSession:
    """一次文件上传的会话。"""

    id: str
    document_id: str
    fingerprint: str
    filename: str
    file_type: str
    size_bytes: int
    chunk_size: int
    total_parts: int
    object_key: str
    s3_upload_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    abort_reason: str | None = None
    claim_version: int = 0
    error_message: str | None = None
    completed_at: datetime | None = None
