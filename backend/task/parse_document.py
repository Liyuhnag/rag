# -*- coding: utf-8 -*-
"""解析任务 Celery 入口。

parse_document 负责解析编排，合并、切分、索引由各自独立任务入口处理。
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
from services.document.versioning import fail_version_mismatch
from services.indexing.parsing.poll import resume_parts
from services.indexing.parsing.service import DocumentParsingService
from services.indexing.parsing.service import SubmittedParse
from services.task_runtime.lease import TaskLease
from services.task_runtime.lifecycle import advance
from services.task_runtime.lifecycle import fail
from services.task_runtime.policy import MINERU_POLL_SECONDS
from services.task_runtime.policy import policy_for
from task.app import celery_app

logger = logging.getLogger("rag.task")
_PARSE_CLAIM_STAGES = (
    DocumentTaskStage.PENDING,
    DocumentTaskStage.PARSING,
)


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
        if document.file_type not in {"pdf", "doc", "docx"} or not resume_parts(task, lease):
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


def _claim_task(task_id: str) -> DocumentTask | None:
    """条件抢占 PENDING 任务。"""
    return task_orm.claim(task_id, _now(), _PARSE_CLAIM_STAGES)


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
