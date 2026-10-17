# -*- coding: utf-8 -*-
"""上传请求的边界校验。"""

from fastapi import UploadFile

from api.errors import AppError
from config import ALLOWED_FILE_TYPES
from config import DEFAULT_OPERATOR


def normalize_filename(filename: str | None) -> str:
    if not filename:
        raise AppError(422, "VALIDATION_ERROR", "文件名不合法")
    cleaned = filename.replace("\\", "/").split("/")[-1].strip()
    if cleaned in {"", ".", ".."} or "\x00" in cleaned:
        raise AppError(422, "VALIDATION_ERROR", "文件名不合法")
    if len(cleaned) > 255:
        raise AppError(422, "VALIDATION_ERROR", "文件名过长")
    return cleaned


def extract_file_type(filename: str) -> str:
    if "." not in filename:
        raise AppError(422, "UNSUPPORTED_FILE_TYPE", "不支持的文件类型")
    extension = filename.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_FILE_TYPES:
        raise AppError(
            422,
            "UNSUPPORTED_FILE_TYPE",
            "不支持的文件类型",
            {"allowed": sorted(ALLOWED_FILE_TYPES)},
        )
    return extension


def normalize_operator(operator: str | None) -> str:
    if operator is None:
        return DEFAULT_OPERATOR
    cleaned = operator.strip()
    if not cleaned:
        return DEFAULT_OPERATOR
    if len(cleaned) > 64 or any(ord(char) < 32 for char in cleaned):
        raise AppError(422, "VALIDATION_ERROR", "操作人名称不合法")
    return cleaned


async def read_limited(upload: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining > 0:
        block = await upload.read(min(1024 * 1024, remaining))
        if not block:
            break
        chunks.append(block)
        remaining -= len(block)
    return b"".join(chunks)
