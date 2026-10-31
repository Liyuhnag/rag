# -*- coding: utf-8 -*-
"""把一份 Word 下载到本地，确认不超过单次上限后提交。"""

import tempfile
from pathlib import Path

from services.parsing.errors import ParseFailed
from services.parsing.mineru import MinerUClient
from services.parsing.mineru.submit import submit_local_file
from services.parsing.models import ParseInput
from services.parsing.models import ParseSubmission
from services.parsing.ports import ObjectReader

_TOO_LARGE = "Word 超过 180MiB，不能直接提交"


def submit_office_file(
    mineru: MinerUClient,
    reader: ObjectReader,
    source: ParseInput,
    max_bytes: int,
) -> ParseSubmission:
    """Word 没有可切的页，超限直接失败，未超限整份提交。"""
    if source.request.size_bytes > max_bytes:
        raise ParseFailed(_TOO_LARGE)
    filename = _office_name(source.request.filename)
    with tempfile.TemporaryDirectory(prefix="word-") as tmp:
        path = Path(tmp) / filename
        reader.download_object(source.request.object_key, path)
        if path.stat().st_size > max_bytes:
            raise ParseFailed(_TOO_LARGE)
        return submit_local_file(
            mineru,
            path,
            filename,
            source.request.object_key,
        )


def _office_name(filename: str) -> str:
    """只用文件名本身，避免临时路径跑出目录。"""
    name = Path(filename).name
    if name in {"", ".", ".."}:
        return "document"
    return name
