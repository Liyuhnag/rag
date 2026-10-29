# -*- coding: utf-8 -*-
"""本地解析和 MinerU 合并共用的索引完成步骤。"""

from datetime import datetime
from datetime import timezone

from model.document_task import orm as task_orm
from model.document_task.model import DocumentTaskStage


def enqueue_indexing(task_id: str) -> None:
    """先持久化可恢复的 INDEXING 阶段，再发送到独立队列。"""
    from task.index_document import index_document

    task_orm.update_progress(
        task_id,
        DocumentTaskStage.INDEXING,
        90,
        "等待向量化与索引写入",
        datetime.now(timezone.utc),
    )
    index_document.apply_async(args=[task_id], queue="index")
