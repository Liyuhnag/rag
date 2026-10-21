# -*- coding: utf-8 -*-
"""文档处理任务模型，对应 document_tasks 表的一行。"""

from dataclasses import dataclass


@dataclass
class DocumentTask:
    """一次文档解析/索引任务。"""

    id: str
    document_id: str
    version: int
    status: str
    stage: str
    progress: int
    step: str
    error_message: str | None = None
