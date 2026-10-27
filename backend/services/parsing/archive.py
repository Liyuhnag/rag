# -*- coding: utf-8 -*-
"""Preflight ZIP central-directory bounds before ZipFile allocates its index."""

import struct
from pathlib import Path

from services.parsing.errors import ParseFailed


def check_archive_directory(
    path: Path,
    *,
    label: str,
    max_bytes: int = 2 * 1024 * 1024 * 1024,
    max_files: int = 10000,
    max_directory_bytes: int = 16 * 1024 * 1024,
) -> None:
    """Read a bounded EOCD tail; reject split archives and ZIP64 explicitly."""
    size = path.stat().st_size
    if size > max_bytes:
        raise ParseFailed(f"{label} 压缩包过大")
    with path.open("rb") as handle:
        handle.seek(max(0, size - 65557))
        tail = handle.read(65557)
    index = tail.rfind(b"PK\x05\x06")
    if index < 0 or len(tail) - index < 22:
        raise ParseFailed(f"{label} 压缩包无法解析")
    record = struct.unpack_from("<4s4H2LH", tail, index)
    _, disk, directory_disk, count_disk, count, directory_size, offset, comment = record
    if index + 22 + comment != len(tail):
        raise ParseFailed(f"{label} 压缩包目录无效")
    if disk or directory_disk or count_disk != count:
        raise ParseFailed(f"{label} 不支持分卷压缩包")
    if count == 0xFFFF or directory_size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
        raise ParseFailed(f"{label} 不支持 ZIP64 压缩包")
    if count > max_files or directory_size > max_directory_bytes:
        raise ParseFailed(f"{label} 文件过多或目录过大")
    absolute_eocd = size - len(tail) + index
    if offset + directory_size > absolute_eocd:
        raise ParseFailed(f"{label} 压缩包目录无效")
    # ZipFile derives directory extent from EOCD position; bound this too so
    # a forged short declared length cannot bypass the directory budget.
    if absolute_eocd - offset > max_directory_bytes:
        raise ParseFailed(f"{label} 压缩包目录过大")
