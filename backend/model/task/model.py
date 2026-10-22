# -*- coding: utf-8 -*-
"""处理任务模型，对应 processing_tasks 表的一行。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ProcessingTask:
    """一次文档异步处理。"""

    id: str
    name: str
    file_type: str
    size_bytes: int
    temp_path: str
    status: str
    document_id: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
