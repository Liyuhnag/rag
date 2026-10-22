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
from services.common.exceptions import FeatureNotReady
from services.parsing.service import DocumentParsingService
from services.parsing.service import SubmittedParse
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

    now = _now()
    service = DocumentParsingService()
    try:
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
        task_orm.complete(task_id, "解析完成", now)
    except FeatureNotReady as exc:
        # 解析、上传或落库尚未实现时保留原有联调行为。
        task_orm.complete(task_id, str(exc), now)
    except Exception as exc:
        message = str(exc) or "解析失败"
        logger.exception("解析任务失败: %s", task_id)
        task_orm.fail(task_id, message, now)
        document_orm.mark_failed(document.id, message, now)
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
