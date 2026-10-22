# -*- coding: utf-8 -*-
"""文档处理任务模型，对应 document_tasks 表的一行。"""

from dataclasses import dataclass
from enum import Enum


class DocumentTaskStatus(str, Enum):
    """解析/索引任务状态。"""

    PENDING = "PENDING"      # 排队等待执行
    RUNNING = "RUNNING"      # Worker 正在执行
    COMPLETED = "COMPLETED"  # 执行成功
    FAILED = "FAILED"        # 执行失败


class DocumentTaskStage(str, Enum):
    """解析/索引任务阶段。"""

    PENDING = "PENDING"      # 等待处理
    SPLITTING = "SPLITTING"  # 拆分文件
    PARSING = "PARSING"      # 解析内容
    MERGING = "MERGING"      # 合并解析结果
    CHUNKING = "CHUNKING"    # 文本切分
    INDEXING = "INDEXING"    # 建立索引
    DONE = "DONE"            # 全部完成


@dataclass
class DocumentTask:
    """一次文档解析/索引任务。"""

    id: str
    document_id: str
    version: int
    status: DocumentTaskStatus
    stage: DocumentTaskStage
    progress: int
    step: str
    error_message: str | None = None
