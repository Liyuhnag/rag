# -*- coding: utf-8 -*-
"""本地解析和 MinerU 合并共用的索引完成步骤。"""

from contextlib import nullcontext
from typing import TYPE_CHECKING
from datetime import datetime
from datetime import timezone

from model.document_task import orm as task_orm
from model.document_task.model import DocumentTaskStage

if TYPE_CHECKING:
    from services.task_runtime.lease import TaskLease


def enqueue_indexing(
    task_id: str, lease_token: str, lease: "TaskLease | None" = None
) -> None:
    """事务内持久化 INDEXING，提交后再投递；续租已失效时不能交接。"""
    from task.index_document import index_document

    with lease.writes() if lease is not None else nullcontext():
        changed = task_orm.update_progress(
            task_id,
            DocumentTaskStage.INDEXING,
            90,
            "等待向量化与索引写入",
            datetime.now(timezone.utc),
            lease_token,
        )
    if changed:
        index_document.apply_async(args=[task_id], queue="index")
