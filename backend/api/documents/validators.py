# -*- coding: utf-8 -*-
"""文档接口的参数校验。"""

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
