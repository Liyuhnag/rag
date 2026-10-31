# -*- coding: utf-8 -*-
"""解析任务 Celery 入口。

parse_document 负责编排，具体动作由同文件辅助函数和解析服务完成。
"""

import logging
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from uuid import uuid4

from model.document import orm as document_orm
from model.document.model import Document
from model.document_task import orm as task_orm
from model.document_task.lease import LeaseLost
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from model.parse_job import orm as parse_job_orm
from model.parse_job.model import ParseJob
from model.parse_job.model import ParseJobStatus
from services.chunking.service import DocumentChunkingService
from services.document.versioning import fail_version_mismatch
from services.parsing.poll import resume_parts
from services.parsing.poll import run_merge
from services.parsing.service import DocumentParsingService
from services.parsing.service import SubmittedParse
from services.parsing.service import StoredParseResult
from services.structured.service import StructuredSchemaService
from services.task_runtime.lease import TaskLease
from services.task_runtime.lifecycle import advance
from services.task_runtime.lifecycle import fail
from services.task_runtime.policy import PARSE_STAGES
from services.task_runtime.policy import MINERU_POLL_SECONDS
from services.task_runtime.policy import policy_for
from task.app import celery_app

logger = logging.getLogger("rag.task")


@celery_app.task(
    name="rag.parse_document",
    ignore_result=True,
    queue="parse",
    soft_time_limit=policy_for(DocumentTaskStage.PARSING).soft_time_limit,
    time_limit=policy_for(DocumentTaskStage.PARSING).time_limit,
)
def parse_document(task_id: str) -> None:
    """领取任务、准备文档并执行解析。"""
    task = _claim_task(task_id)
    if task is None:
        return

    if not task.lease_token:
        logger.error("任务缺少领取标记: %s", task_id)
        return
    try:
        with TaskLease(task.id, task.lease_token) as lease:
            _run(task, lease)
    except LeaseLost:
        logger.info("解析任务已失去执行权，停止写入: %s", task_id)


def _run(task: DocumentTask, lease: TaskLease) -> None:
    document = _load_document(task, lease)
    if document is None:
        return
    try:
        with lease.writes():
            if document.parse_version != task.version:
                fail_version_mismatch(task, _now())
                return
        if task.stage == DocumentTaskStage.MERGING:
            run_merge(task, document, lease)
        elif task.stage == DocumentTaskStage.CHUNKING:
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
        elif document.file_type not in {"pdf", "doc", "docx"} or not resume_parts(task, lease):
            _parse(task, document, lease)
    except LeaseLost:
        raise
    except Exception as exc:
        # worker 是业务异常边界；失败与文档状态在同一事务内提交。
        logger.exception("解析任务失败: %s", task.id)
        fail(task, lease, str(exc) or "解析失败")
        raise


def _parse(task: DocumentTask, document: Document, lease: TaskLease) -> None:
    with lease.writes():
        if not document_orm.begin_parse(document.id, task.version, _now()):
            fail_version_mismatch(task, _now())
            return
    service = DocumentParsingService(attempt_id=lease.token)
    service.task_id = task.id
    service.lease_token = lease.token
    lease.ensure()
    outcome = service.parse(document, task.version)
    if isinstance(outcome, SubmittedParse):
        with lease.writes():
            # PDF 在每一段提交成功时已经写入 parse_jobs。
            if document.file_type != "pdf":
                _record_parts(task, outcome, _now())
            task_orm.update_progress(
                task.id, DocumentTaskStage.PARSING, 5, "分段已提交", _now(), lease.token
            )
        return
    lease.ensure()
    stored = service.upload(outcome.parsed)

    def publish() -> None:
        service.persist(outcome.parsed, stored)

    advance(lease, DocumentTaskStage.CHUNKING, 85, "等待切分", prepare=publish)


def _chunk(
    task: DocumentTask, document: Document, stored: StoredParseResult, lease: TaskLease
) -> None:
    """三段式切分：前置检查（持锁）→ 切分计算（无锁）→ 写库完成（持锁）。"""
    structured = document.file_type in {"csv", "xlsx"}

    if structured:
        # 结构化文档（CSV/Excel）仍用原路径，保持简单
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
    try:
        chunks = service.compute_chunks(
            document, task.version, stored.md_key, stored.json_key, heartbeat=heartbeat
        )
    except Exception:
        # 切分失败在外层 _run 的 except 里统一处理
        raise

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


def _claim_task(task_id: str) -> DocumentTask | None:
    """条件抢占 PENDING 任务。"""
    return task_orm.claim(task_id, _now(), PARSE_STAGES)


def _load_document(task: DocumentTask, lease: TaskLease) -> Document | None:
    """读取文档；文档不存在时任务直接失败。"""
    document = document_orm.get(task.document_id)
    if document is None:
        with lease.writes():
            task_orm.fail(task.id, "文档不存在", _now(), lease.token)
    return document


def _record_parts(
    task: DocumentTask,
    outcome: SubmittedParse,
    now: datetime,
) -> None:
    """把已提交的分段记下来，轮询时才能按 job_id 继续查。"""
    for part in outcome.parts:
        parse_job_orm.insert(
            ParseJob(
                id=uuid4().hex,
                document_id=task.document_id,
                task_id=task.id,
                version=task.version,
                lease_token=task.lease_token,
                part_index=part.part_index,
                page_start=part.page_start,
                page_end=part.page_end,
                source_key=part.source_key,
                mineru_file_id=part.file_id,
                mineru_job_id=part.job_id,
                status=ParseJobStatus.SUBMITTED,
                attempts=1,
                next_poll_at=now + timedelta(seconds=MINERU_POLL_SECONDS),
                error_message=None,
                result_key=None,
                created_at=now,
                updated_at=now,
            )
        )


def _now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)
