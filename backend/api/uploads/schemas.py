# -*- coding: utf-8 -*-
"""大文件上传接口的请求和响应。"""

from datetime import datetime

from api.common.schemas import APIModel


class UploadInitRequest(APIModel):
    filename: str
    size_bytes: int
    fingerprint: str


class UploadedPart(APIModel):
    part_number: int
    size_bytes: int


class UploadInitResponse(APIModel):
    upload_id: str
    document_id: str
    status: str
    chunk_size: int
    total_parts: int
    uploaded_parts: list[UploadedPart]
    expires_at: datetime
    task_id: str | None = None
    task_status: str | None = None


class UploadStatusResponse(APIModel):
    upload_id: str
    document_id: str
    filename: str
    size_bytes: int
    chunk_size: int
    total_parts: int
    status: str
    uploaded_parts: list[UploadedPart]
    expires_at: datetime
    error_message: str | None = None
    task_id: str | None = None
    task_status: str | None = None


class UploadPartResponse(APIModel):
    part_number: int
    size_bytes: int


class UploadCompleteResponse(APIModel):
    upload_id: str
    document_id: str
    status: str
    task_id: str | None = None
    task_status: str | None = None
