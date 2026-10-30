# -*- coding: utf-8 -*-
"""MinerU 分段轮询、重新提交、下载和合并。"""

import hashlib
import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

from model.document import orm as document_orm
from model.document_task import orm as task_orm
from model.document_task.model import DocumentTaskStage
from model.parse_job import orm as parse_job_orm
from model.parse_job.model import ParseJob
from model.parse_job.model import ParseJobStatus
from services.chunking.service import DocumentChunkingService
from services.indexing.flow import enqueue_indexing
from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.collect import PartZip
from services.parsing.errors import MinerUJobLost
from services.parsing.errors import MinerURequestError
from services.parsing.errors import ParserUnavailable
from services.parsing.mineru.client import MinerUClient
from services.parsing.ports import ObjectReader
from services.parsing.service import DocumentParsingService

logger = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(seconds=10)
DOWNLOAD_LEASE = timedelta(seconds=300)
RESUBMIT_LEASE = timedelta(seconds=300)
MAX_ATTEMPTS = 3
POLL_BATCH = 50


@dataclass(frozen=True)
class PollPlan:
    """一次轮询之后要对分段做的事。"""

    status: ParseJobStatus
    next_poll_at: datetime | None
    error_message: str | None
    resubmit: bool
    download: bool
    fail_document: bool


def poll_due(now: datetime, mineru: MinerUClient | None = None) -> int:
    """查询到期分段的 MinerU 状态，并安排下载或重新提交。"""
    jobs = parse_job_orm.claim_due(now, POLL_BATCH)
    client = mineru or MinerUClient()
    for job in jobs:
        try:
            poll_job(job, client, now)
        except Exception:
            logger.exception("轮询分段失败: %s", job.id)
    return len(jobs)


def poll_job(job: ParseJob, mineru: MinerUClient, now: datetime) -> None:
    """按 MinerU 状态决定继续等、下载、重试或失败。"""
    if job.status == ParseJobStatus.DOWNLOADING:
        _enqueue_download(job.id)
        return
    if not job.mineru_job_id:
        apply_plan(job, plan_lost(job, now), now)
        return
    try:
        remote = mineru.status(job.mineru_job_id)
    except MinerUJobLost:
        plan = plan_lost(job, now)
    except ParserUnavailable as exc:
        plan = plan_unavailable(job, exc.message, now)
    except MinerURequestError as exc:
        plan = plan_rejected(job, exc.message, now)
    else:
        plan = plan_status(job, remote.state, remote.error, now)
    apply_plan(job, plan, now)


def plan_status(
    job: ParseJob,
    state: str,
    error: str | None,
    now: datetime,
) -> PollPlan:
    """把 MinerU 状态翻译成分段动作。"""
    if state == "queued":
        return _wait(ParseJobStatus.SUBMITTED, now)
    if state == "running":
        return _wait(ParseJobStatus.RUNNING, now)
    if state == "completed":
        return PollPlan(
            status=ParseJobStatus.DOWNLOADING,
            next_poll_at=now + DOWNLOAD_LEASE,
            error_message=None,
            resubmit=False,
            download=True,
            fail_document=False,
        )
    return _plan_failure(job, error or "MinerU 解析失败", now)


def plan_lost(job: ParseJob, now: datetime) -> PollPlan:
    """任务索引丢失时按失败次数决定重试。"""
    return _plan_failure(job, "MinerU 任务不存在", now)


def plan_unavailable(job: ParseJob, message: str, now: datetime) -> PollPlan:
    """MinerU 暂时不可用。推迟下次查询，不计入解析失败。"""
    return PollPlan(
        status=job.status,
        next_poll_at=now + _backoff(job.attempts),
        error_message=message,
        resubmit=False,
        download=False,
        fail_document=False,
    )


def plan_rejected(job: ParseJob, message: str, now: datetime) -> PollPlan:
    """请求被拒绝时不再重试。"""
    return PollPlan(
        status=ParseJobStatus.FAILED,
        next_poll_at=None,
        error_message=message,
        resubmit=False,
        download=False,
        fail_document=True,
    )


def apply_plan(job: ParseJob, plan: PollPlan, now: datetime) -> None:
    """把轮询结论写入分段任务，并投递下载或重新提交。"""
    if plan.fail_document:
        parse_job_orm.set_status(
            job.id,
            ParseJobStatus.FAILED,
            now,
            None,
            plan.error_message,
        )
        _fail_document(job, plan.error_message or "解析失败", now)
        return
    if plan.resubmit:
        parse_job_orm.mark_retry(
            job.id,
            plan.next_poll_at or (now + RESUBMIT_LEASE),
            plan.error_message,
            now,
        )
        _enqueue_resubmit(job.id)
        return
    parse_job_orm.set_status(
        job.id,
        plan.status,
        now,
        plan.next_poll_at,
        plan.error_message,
    )
    if plan.download:
        _enqueue_download(job.id)


def resubmit_job(
    job_id: str,
    mineru: MinerUClient,
    reader: ObjectReader,
    now: datetime,
) -> None:
    """用分段原文件再向 MinerU 提交一次。"""
    job = parse_job_orm.get(job_id)
    if job is None or job.status != ParseJobStatus.SUBMITTED:
        return
    filename = Path(job.source_key).name
    if filename in {"", ".", ".."}:
        apply_plan(job, plan_rejected(job, "分段文件名无效", now), now)
        return
    try:
        submitted = _submit_source(job, filename, mineru, reader)
    except MinerURequestError as exc:
        apply_plan(job, plan_rejected(job, exc.message, now), now)
        return
    except ParserUnavailable as exc:
        apply_plan(job, plan_unavailable(job, exc.message, now), now)
        return
    except Exception as exc:
        logger.exception("重新提交分段失败: %s", job_id)
        apply_plan(job, plan_unavailable(job, str(exc) or "重新提交失败", now), now)
        return
    parse_job_orm.record_submission(
        job.id,
        job.mineru_job_id,
        submitted.file_id,
        submitted.job_id,
        now + POLL_INTERVAL,
        now,
    )


def download_job(
    job_id: str,
    mineru: MinerUClient,
    reader: ObjectReader,
    now: datetime,
) -> None:
    """下载分段压缩包。同一文档的分段都到齐后合并写入对象存储。"""
    job = parse_job_orm.get(job_id)
    if job is None or job.status != ParseJobStatus.DOWNLOADING:
        return
    if not job.result_key:
        if not _store_zip(job, mineru, reader, now):
            return
    merge_ready(job, reader, now)


def merge_ready(job: ParseJob, reader: ObjectReader, now: datetime) -> None:
    """全部分段都已下载时，由一个任务完成合并。"""
    jobs = parse_job_orm.list_by_document_version(job.document_id, job.version)
    if not _all_downloaded(jobs):
        return
    if not task_orm.begin_merge(job.task_id, now):
        return
    document = document_orm.get(job.document_id)
    if document is None:
        _fail_document(job, "文档不存在", now)
        return
    if document.parse_version != job.version:
        return
    parts: list[PartZip] = []
    for item in jobs:
        if not item.result_key:
            _fail_document(job, "分段结果缺失", now)
            return
        parts.append(
            PartZip(
                part_index=item.part_index,
                page_start=item.page_start,
                page_end=item.page_end,
                result_key=item.result_key,
            )
        )
    try:
        stored = DocumentParsingService(reader).collect(
            document.id,
            job.version,
            document.file_type,
            document.object_key,
            parts,
        )
        now = datetime.now(timezone.utc)
        document_orm.set_parsed_artifacts(
            document.id,
            stored.md_key,
            stored.json_key,
            now,
            expected_version=job.version,
        )
        keys = dict(stored.part_results)
        for item in jobs:
            parse_job_orm.record_result(
                item.id,
                keys.get(item.part_index, ""),
                now,
            )
        task_orm.update_progress(
            job.task_id,
            DocumentTaskStage.CHUNKING,
            85,
            "文档切分中",
            datetime.now(timezone.utc),
        )
        result = DocumentChunkingService().run(
            document, job.version, stored.md_key, stored.json_key
        )
        if result is False:
            return
        enqueue_indexing(job.task_id)
    except Exception as exc:
        # 合并、切分和索引是同一个任务边界，任一步失败都不能完成任务。
        message = str(exc) or "合并失败"
        logger.exception("合并分段失败: %s", job.task_id)
        _fail_document(job, message, datetime.now(timezone.utc))
        raise


def _wait(status: ParseJobStatus, now: datetime) -> PollPlan:
    return PollPlan(
        status=status,
        next_poll_at=now + POLL_INTERVAL,
        error_message=None,
        resubmit=False,
        download=False,
        fail_document=False,
    )


def _plan_failure(job: ParseJob, message: str, now: datetime) -> PollPlan:
    if job.attempts >= MAX_ATTEMPTS:
        return plan_rejected(job, message, now)
    return PollPlan(
        status=ParseJobStatus.SUBMITTED,
        next_poll_at=now + RESUBMIT_LEASE,
        error_message=message,
        resubmit=True,
        download=False,
        fail_document=False,
    )


def _backoff(attempts: int) -> timedelta:
    exponent = min(max(attempts, 0), 6)
    return timedelta(seconds=min(60, (2**exponent) * 5))


def _submit_source(job, filename, mineru, reader):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / filename
        reader.download_object(job.source_key, path)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(65536):
                digest.update(chunk)
        return mineru.submit(path, filename, digest.hexdigest())


def _store_zip(job, mineru, reader, now: datetime) -> bool:
    """下载并保存压缩包。失败时改下次轮询时间，返回是否可以继续合并。"""
    if not job.mineru_job_id:
        apply_plan(job, plan_lost(job, now), now)
        return False
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = mineru.download(job.mineru_job_id, Path(directory))
            key = (
                f"documents/{job.document_id}/parse/{job.version}"
                f"/parts/{job.part_index}/result.zip"
            )
            reader.upload_file(key, path, "application/zip")
    except MinerUJobLost:
        apply_plan(job, plan_lost(job, now), now)
        return False
    except ParserUnavailable as exc:
        parse_job_orm.set_status(
            job.id,
            ParseJobStatus.DOWNLOADING,
            now,
            now + _backoff(job.attempts),
            exc.message,
        )
        return False
    except MinerURequestError as exc:
        apply_plan(job, plan_rejected(job, exc.message, now), now)
        return False
    except Exception as exc:
        # 下载或对象存储故障走现有延迟重试。
        logger.exception("下载或保存分段失败: %s", job.id)
        parse_job_orm.set_status(
            job.id,
            ParseJobStatus.DOWNLOADING,
            now,
            now + _backoff(job.attempts),
            str(exc) or "保存分段失败",
        )
        return False
    parse_job_orm.save_zip(job.id, key, now)
    return True


def _all_downloaded(jobs: list[ParseJob]) -> bool:
    if not jobs:
        return False
    return all(
        item.status == ParseJobStatus.DOWNLOADING and bool(item.result_key)
        for item in jobs
    )


def _fail_document(job: ParseJob, message: str, now: datetime) -> None:
    task_orm.fail(job.task_id, message, now)
    document_orm.mark_failed(
        job.document_id, message, now, expected_version=job.version
    )


def _enqueue_download(job_id: str) -> None:
    from task.poll_parse import download_parse_result

    download_parse_result.delay(job_id)


def _enqueue_resubmit(job_id: str) -> None:
    from task.poll_parse import resubmit_parse_part

    resubmit_parse_part.delay(job_id)


def default_reader() -> StorageObjectReader:
    """Worker 使用的对象存储读取器。"""
    return StorageObjectReader()
