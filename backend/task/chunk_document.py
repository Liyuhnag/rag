# -*- coding: utf-8 -*-
"""切分 worker 自行领取；复用公共租约与失败桥接。"""

import logging
from datetime import datetime
from datetime import timezone

from model.document import orm as document_orm
from model.document.model import Document
from model.document_task import orm as task_orm
from model.document_task.lease import LeaseLost
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from services.chunking.service import DocumentChunkingService
from services.document.versioning import fail_version_mismatch
from services.parsing.service import StoredParseResult
from services.structured.service import StructuredSchemaService
from services.task_runtime.lease import TaskLease
from services.task_runtime.lifecycle import advance
from services.task_runtime.lifecycle import fail
from services.task_runtime.policy import CHUNK_STAGES
from services.task_runtime.policy import policy_for
from task.app import celery_app

logger = logging.getLogger("rag.task")
_policy = policy_for(DocumentTaskStage.CHUNKING)


@celery_app.task(
    name="rag.chunk_document",
    ignore_result=True,
    queue=_policy.queue,
    soft_time_limit=_policy.soft_time_limit,
    time_limit=_policy.time_limit,
)
def chunk_document(task_id: str) -> None:
    """只有 PENDING + CHUNKING 能获得本次执行权。"""
    task = task_orm.claim(task_id, _now(), CHUNK_STAGES)
    if task is None:
        return
    if not task.lease_token:
        logger.error("任务缺少领取标记: %s", task_id)
        return
    try:
        with TaskLease(task.id, task.lease_token, CHUNK_STAGES) as lease:
            _run(task, lease)
    except LeaseLost:
        logger.info("切分任务失去执行权，停止写入: %s", task_id)


def _run(task: DocumentTask, lease: TaskLease) -> None:
    document = document_orm.get(task.document_id)
    if document is None:
        with lease.writes():
            task_orm.fail(task.id, "文档不存在", _now(), lease.token)
        return
    try:
        with lease.writes():
            if document.parse_version != task.version:
                fail_version_mismatch(task, _now())
                return
        completed = (
            document.parsed_md_key
            and document.parsed_json_key
            and document.parsed_version == task.version
        )
        if not completed:
            advance(lease, DocumentTaskStage.PARSING, 0, "解析产物缺失，重新解析")
            return
        stored = StoredParseResult(document.parsed_md_key, document.parsed_json_key)
        _chunk(task, document, stored, lease)
    except LeaseLost:
        raise
    except Exception as exc:
        logger.exception("文档切分失败: %s", task.id)
        fail(task, lease, str(exc) or "切分失败")
        raise


def _chunk(
    task: DocumentTask, document: Document, stored: StoredParseResult, lease: TaskLease
) -> None:
    """三段式切分：前置检查（持锁）→ 切分计算（无锁）→ 写库完成（持锁）。"""
    structured = document.file_type in {"csv", "xlsx"}

    if structured:
        # 结构化文档（CSV/Excel）保存工作表 schema
        def save() -> bool:
            service = StructuredSchemaService()
            if service.run(document, task.version, stored.md_key, stored.json_key) is False:
                fail_version_mismatch(task, _now())
                return False
            return True

        with lease.writes():
            if save():
                task_orm.complete(task.id, "结构已保存，待数据入库", _now(), lease.token)
        return

    # ============ 阶段 1：前置检查（持锁）============
    with lease.writes():
        if document.parse_version != task.version:
            fail_version_mismatch(task, _now())
            return
        # 标记切分中，Recover 看到心跳知道任务正在执行
        task_orm.mark_chunking_in_progress(task.id, lease.token, _now())
    # ← 事务提交，行锁释放

    # ============ 阶段 2：切分计算（无锁）============
    # 在事务外执行 S3 读取和切分，不持有行锁
    def heartbeat(written: int) -> None:
        lease.ensure()  # 只检查租约，不持有行锁
        # 使用轻量级更新，不阻塞其他操作
        task_orm.update_chunking_progress_lightweight(
            task.id, written, lease.token, _now()
        )

    service = DocumentChunkingService()
    chunks = service.compute_chunks(
        document, task.version, stored.md_key, stored.json_key, heartbeat=heartbeat
    )

    if chunks is None:
        # 版本已过期
        with lease.writes():
            fail_version_mismatch(task, _now())
        return

    # ============ 阶段 3：写库完成（持锁）============
    def persist() -> bool:
        # 短事务：只做复核版本 + 替换旧块，不再包含 S3 读取与切分计算
        lease.ensure()
        if not service.persist_chunks(document.id, task.version, chunks):
            fail_version_mismatch(task, _now())
            return False
        return True

    advance(lease, DocumentTaskStage.INDEXING, 90, "等待向量化与索引写入", prepare=persist)


def _now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)
