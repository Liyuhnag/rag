# -*- coding: utf-8 -*-
"""PDF 分段解析任务模型，对应 parse_jobs 表的一行。"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ParseJobStatus(str, Enum):
    """PDF 分段解析任务状态。"""

    PENDING = "PENDING"          # 等待提交
    SUBMITTED = "SUBMITTED"      # 已提交 MinerU
    RUNNING = "RUNNING"          # MinerU 正在处理
    DOWNLOADING = "DOWNLOADING"  # 正在下载解析结果
    COMPLETED = "COMPLETED"      # 分段解析完成
    FAILED = "FAILED"            # 分段解析失败


@dataclass
class ParseJob:
    """一个 PDF 分段解析任务。"""

    id: str
    document_id: str
    task_id: str
    version: int
    part_index: int
    page_start: int
    page_end: int
    source_key: str
    mineru_file_id: str | None
    mineru_job_id: str | None
    status: ParseJobStatus
    attempts: int
    next_poll_at: datetime | None
    error_message: str | None
    result_key: str | None
    created_at: datetime
    updated_at: datetime
