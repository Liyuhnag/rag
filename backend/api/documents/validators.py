# -*- coding: utf-8 -*-
"""文档接口的参数校验。"""

from fastapi import UploadFile

from api.documents.errors import empty_file
from api.documents.errors import file_too_large
from api.documents.errors import filename_too_long
from api.documents.errors import invalid_filename
from api.documents.errors import unsupported_file_type
from config import ALLOWED_FILE_TYPES


def normalize_filename(filename: str | None) -> str:
    if not filename:
        raise invalid_filename()
    cleaned = filename.replace("\\", "/").split("/")[-1].strip()
    if cleaned in {"", ".", ".."} or "\x00" in cleaned:
        raise invalid_filename()
    if len(cleaned) > 255:
        raise filename_too_long()
    return cleaned


def extract_file_type(filename: str) -> str:
    if "." not in filename:
        raise unsupported_file_type()
    extension = filename.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_FILE_TYPES:
        raise unsupported_file_type()
    return extension


async def read_upload(upload: UploadFile, limit: int) -> bytes:
    """读完上传内容，并拒绝空文件和超限文件。"""
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining > 0:
        block = await upload.read(min(1024 * 1024, remaining))
        if not block:
            break
        chunks.append(block)
        remaining -= len(block)
    content = b"".join(chunks)
    if len(content) > limit:
        raise file_too_large()
    if not content:
        raise empty_file()
    return content
