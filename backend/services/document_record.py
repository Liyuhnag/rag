# -*- coding: utf-8 -*-
"""文档记录。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class DocumentRecord:
    """服务返回给 API 的文档记录。"""

    id: str
    name: str
    file_type: str
    size_bytes: int
    chunk_count: int
    status: str
    operator: str
    uploaded_at: datetime
    stored_name: str
