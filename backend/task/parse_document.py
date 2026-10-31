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
from services.common.exceptions import FeatureNotReady
from services.document.versioning import fail_version_mismatch
from services.indexing.flow import enqueue_indexing
from services.parsing.service import DocumentParsingService
from services.parsing.service import SubmittedParse
from services.parsing.service import StoredParseResult
from services.structured.service import StructuredSchemaService
from services.task_runtime.lease import TaskLease
from task.app import celery_app

logger = logging.getLogger("rag.task")
_POLL_SECONDS = 10


@celery_app.task(
    name="rag.parse_document",
    ignore_result=True,
    soft_time_limit=300,
    time_limit=330,
)
def parse_document(task_id: str) -> None:
    """领取任务、准备文档并执行解析。"""
    task = _claim_task(task_id)
    if task is None:
        return

    if not task.lease_token:
        logger.error("任务缺少领取标记: %s", task_id)
        return
    if task.stage == DocumentTaskStage.INDEXING:
        document = document_orm.get(task.document_id)
        if document is None:
            task_orm.fail(task.id, "文档不存在", _now(), task.lease_token)
        elif document.parse_version != task.version:
            fail_version_mismatch(task, _now())
        else:
            enqueue_indexing(task_id, task.lease_token)
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
    with lease.writes():
        if document.parse_version != task.version:
            fail_version_mismatch(task, _now())
            return
    chunking = task.stage == DocumentTaskStage.CHUNKING
    try:
        stored = _parse(task, document, lease, chunking)
        if stored is None:
            return
        chunking = True
        _chunk(task, document, stored, lease)
    except LeaseLost:
        raise
    except Exception as exc:
        # 任务边界统一处理业务异常；失去执行权时不再修改任务或文档。
        with lease.writes():
            if isinstance(exc, FeatureNotReady) and not chunking:
                task_orm.complete(task.id, str(exc), _now(), lease.token)
                return
            message = str(exc) or "解析失败"
            logger.exception("解析任务失败: %s", task.id)
            now = _now()
            if task_orm.fail(task.id, message, now, lease.token):
                document_orm.mark_failed(
                    document.id, message, now, expected_version=task.version
                )
        raise


def _parse(
    task: DocumentTask, document: Document, lease: TaskLease, chunking: bool
) -> StoredParseResult | None:
    if chunking and document.parsed_md_key and document.parsed_json_key:
        return StoredParseResult(document.parsed_md_key, document.parsed_json_key)
    with lease.writes():
        if not document_orm.begin_parse(document.id, task.version, _now()):
            fail_version_mismatch(task, _now())
            return None
    service = DocumentParsingService(attempt_id=lease.token)
    lease.ensure()
    outcome = service.parse(document, task.version)
    if isinstance(outcome, SubmittedParse):
        with lease.writes():
            _record_parts(task, outcome, _now())
            task_orm.update_progress(
                task.id, DocumentTaskStage.PARSING, 5, "分段已提交", _now(), lease.token
            )
        return None
    lease.ensure()
    stored = service.upload(outcome.parsed)
    with lease.writes():
        service.persist(outcome.parsed, stored)
    return stored


def _chunk(
    task: DocumentTask, document: Document, stored: StoredParseResult, lease: TaskLease
) -> None:
    structured = document.file_type in {"csv", "xlsx"}
    with lease.writes():
        task_orm.update_progress(
            task.id,
            DocumentTaskStage.CHUNKING,
            85,
            "表结构保存中" if structured else "文档切分中",
            _now(),
            lease.token,
        )
    with lease.writes():
        service = StructuredSchemaService() if structured else DocumentChunkingService()
        result = service.run(document, task.version, stored.md_key, stored.json_key)
        if result is False:
            fail_version_mismatch(task, _now())
            return
        if structured:
            task_orm.complete(task.id, "结构已保存，待数据入库", _now(), lease.token)
            return
    lease.ensure()
    enqueue_indexing(task.id, lease.token, lease)


def _claim_task(task_id: str) -> DocumentTask | None:
    """条件抢占 PENDING 任务。"""
    return task_orm.claim(task_id, _now())


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
                next_poll_at=now + timedelta(seconds=_POLL_SECONDS),
                error_message=None,
                result_key=None,
                created_at=now,
                updated_at=now,
            )
        )


def _now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)
