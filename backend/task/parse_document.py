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
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from model.parse_job import orm as parse_job_orm
from model.parse_job.model import ParseJob
from model.parse_job.model import ParseJobStatus
from services.chunking.service import DocumentChunkingService
from services.common.exceptions import FeatureNotReady
from services.parsing.service import DocumentParsingService
from services.parsing.service import SubmittedParse
from services.parsing.service import StoredParseResult
from services.structured.service import StructuredSchemaService
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

    document = _load_document(task)
    if document is None:
        return

    if document.parse_version != task.version:
        return

    now = _now()
    chunking = task.stage == DocumentTaskStage.CHUNKING
    try:
        if chunking and document.parsed_md_key and document.parsed_json_key:
            stored = StoredParseResult(document.parsed_md_key, document.parsed_json_key)
        else:
            service = DocumentParsingService()
            outcome = service.parse(document, task.version)
            if isinstance(outcome, SubmittedParse):
                _record_parts(task, outcome, now)
                task_orm.update_progress(
                    task_id,
                    DocumentTaskStage.PARSING,
                    5,
                    "分段已提交",
                    now,
                )
                return
            stored = service.upload(outcome.parsed)
            service.persist(outcome.parsed, stored)
        chunking = True
        if document.file_type in {"csv", "xlsx"}:
            task_orm.update_progress(
                task_id, DocumentTaskStage.CHUNKING, 85, "表结构保存中", _now()
            )
            result = StructuredSchemaService().run(
                document, task.version, stored.md_key, stored.json_key
            )
            if result is False:
                return
            task_orm.complete(task_id, "结构已保存，待数据入库", _now())
            return
        task_orm.update_progress(
            task_id, DocumentTaskStage.CHUNKING, 85, "文档切分中", _now()
        )
        result = DocumentChunkingService().run(
            document, task.version, stored.md_key, stored.json_key
        )
        if result is False:
            return
        task_orm.complete(task_id, "解析与切分完成", _now())
    except Exception as exc:
        # 任务边界统一记录解析或切分失败，再交由 worker 处理异常。
        if isinstance(exc, FeatureNotReady) and not chunking:
            task_orm.complete(task_id, str(exc), _now())
            return
        message = str(exc) or "解析失败"
        logger.exception("解析任务失败: %s", task_id)
        now = _now()
        task_orm.fail(task_id, message, now)
        document_orm.mark_failed(
            document.id, message, now, expected_version=task.version
        )
        raise


def _claim_task(task_id: str) -> DocumentTask | None:
    """条件抢占 PENDING 任务。"""
    return task_orm.claim(task_id, _now())


def _load_document(task: DocumentTask) -> Document | None:
    """读取文档；文档不存在时任务直接失败。"""
    document = document_orm.get(task.document_id)
    if document is None:
        task_orm.fail(task.id, "文档不存在", _now())
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
