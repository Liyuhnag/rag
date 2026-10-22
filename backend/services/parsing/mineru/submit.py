# -*- coding: utf-8 -*-
"""把一整份文件交给 MinerU。大小是否允许由调用方决定。"""

import hashlib
import tempfile
from pathlib import Path

from services.parsing.mineru.client import MinerUClient
from services.parsing.mineru.client import MinerUJob
from services.parsing.models import ParseSubmission
from services.parsing.models import SubmittedPart


def submit_whole_file(
    mineru: MinerUClient,
    content: bytes,
    filename: str,
    object_key: str,
) -> ParseSubmission:
    """计算摘要、提交文件，并记成一个尚未测量页码的分段。"""
    digest = hashlib.sha256(content).hexdigest()
    safe_name = _local_name(filename)
    with tempfile.TemporaryDirectory(prefix="mineru-") as tmp:
        path = Path(tmp) / safe_name
        path.write_bytes(content)
        job = mineru.submit(path, safe_name, digest)
    return _submission(job, object_key)


def _local_name(filename: str) -> str:
    """只用文件名本身，避免临时路径跑出目录。"""
    name = Path(filename).name
    if name in {"", ".", ".."}:
        return "document"
    return name


def _submission(job: MinerUJob, object_key: str) -> ParseSubmission:
    """整文件提交时页码还没测量，page_end 用 0。"""
    return ParseSubmission(
        parts=(
            SubmittedPart(
                file_id=job.file_id,
                job_id=job.job_id,
                part_index=1,
                page_start=1,
                page_end=0,
                source_key=object_key,
            ),
        )
    )
