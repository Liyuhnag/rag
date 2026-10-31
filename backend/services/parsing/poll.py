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
from uuid import uuid4

from model.document import orm as document_orm
from model.document_task import orm as task_orm
from model.document_task.lease import LeaseLost
from model.document_task.model import DocumentTaskStage
from model.parse_job import orm as parse_job_orm
from model.parse_job.model import ParseJob
from model.parse_job.model import ParseJobStatus
from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.collect import PartZip
from services.parsing.errors import MinerUJobLost
from services.parsing.errors import MinerURequestError
from services.parsing.errors import ParserUnavailable
from services.parsing.mineru.client import MinerUClient
from services.parsing.ports import ObjectReader
from services.parsing.service import DocumentParsingService
from services.task_runtime.lease import TaskLease
from services.task_runtime.lifecycle import advance
from services.task_runtime.policy import MINERU_POLL_SECONDS

logger = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(seconds=MINERU_POLL_SECONDS)
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
    try:
        with _lease(job, parsing_only=True) as lease:
            _poll_job(job, mineru, now, lease)
    except LeaseLost:
        return


def _poll_job(
    job: ParseJob, mineru: MinerUClient, now: datetime, lease: TaskLease
) -> None:
    # 1. 重提租约到期后才会被 claim_due 领取到这里，说明上一条消息丢失或失败，补投一次
    if job.status == ParseJobStatus.RESUBMITTING:
        _enqueue_resubmit(job.id, job.lease_token)
        return

    # 2. 在下载队列中，投递下载消息
    if job.status == ParseJobStatus.DOWNLOADING:
        _enqueue_download(job.id, job.lease_token)
        return

    # 3. 发现需要重提，先改状态为 RESUBMITTING，再投递
    if not job.mineru_job_id:
        with lease.writes():
            parse_job_orm.set_status(
                job.id,
                ParseJobStatus.RESUBMITTING,
                now,
                now + RESUBMIT_LEASE,
                "等待重新提交",
            )
        _enqueue_resubmit(job.id, job.lease_token)
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
    apply_plan(job, plan, now, lease)


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


def apply_plan(
    job: ParseJob, plan: PollPlan, now: datetime, lease: TaskLease | None = None
) -> None:
    """把轮询结论写入分段任务，并投递下载或重新提交。"""
    try:
        lease = lease or _lease(job, parsing_only=True)
        with lease.writes():
            _save_plan(job, plan, now, lease)
    except LeaseLost:
        return
    if plan.resubmit:
        _enqueue_resubmit(job.id, job.lease_token)
    elif plan.download:
        _enqueue_download(job.id, job.lease_token)


def _save_plan(job: ParseJob, plan: PollPlan, now: datetime, lease: TaskLease) -> None:
    if plan.fail_document:
        parse_job_orm.set_status(
            job.id,
            ParseJobStatus.FAILED,
            now,
            None,
            plan.error_message,
        )
        _fail_document(job, plan.error_message or "解析失败", now, lease)
        return
    if plan.resubmit:
        parse_job_orm.mark_retry(
            job.id,
            plan.next_poll_at or (now + RESUBMIT_LEASE),
            plan.error_message,
            now,
        )
        return
    parse_job_orm.set_status(
        job.id,
        plan.status,
        now,
        plan.next_poll_at,
        plan.error_message,
    )


def resubmit_job(
    job_id: str,
    mineru: MinerUClient,
    reader: ObjectReader,
    now: datetime,
    lease_token: str | None = None,
) -> None:
    """用分段原文件再向 MinerU 提交一次。"""
    job = parse_job_orm.get(job_id)
    if job is None or job.status != ParseJobStatus.RESUBMITTING:
        return
    if lease_token is not None and job.lease_token != lease_token:
        return
    try:
        with _lease(job, parsing_only=True) as lease:
            _resubmit(job, mineru, reader, now, lease)
    except LeaseLost:
        return


def _resubmit(job: ParseJob, mineru, reader, now: datetime, lease: TaskLease) -> None:
    filename = Path(job.source_key).name
    if filename in {"", ".", ".."}:
        apply_plan(job, plan_rejected(job, "分段文件名无效", now), now, lease)
        return
    try:
        submitted = _submit_source(job, filename, mineru, reader)
    except MinerURequestError as exc:
        apply_plan(job, plan_rejected(job, exc.message, now), now, lease)
        return
    except ParserUnavailable as exc:
        apply_plan(job, plan_unavailable(job, exc.message, now), now, lease)
        return
    except Exception as exc:
        logger.exception("重新提交分段失败: %s", job.id)
        apply_plan(
            job, plan_unavailable(job, str(exc) or "重新提交失败", now), now, lease
        )
        return
    with lease.writes():
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
    lease_token: str | None = None,
) -> None:
    """下载分段，齐备后交接独立 MERGING 消息。"""
    job = parse_job_orm.get(job_id)
    if job is None or job.status != ParseJobStatus.DOWNLOADING:
        return
    if lease_token is not None and job.lease_token != lease_token:
        return
    try:
        with _lease(job, parsing_only=True) as lease:
            if not job.result_key and not _store_zip(job, mineru, reader, now, lease):
                return
        merge_ready(job, reader, now)
    except LeaseLost:
        return


def merge_ready(job: ParseJob, reader: ObjectReader, now: datetime) -> None:
    """最后一个 ZIP 到齐后只交接，不在下载 worker 内执行合并。"""
    try:
        with _lease(job, parsing_only=True) as lease:
            with lease.writes():
                jobs = parse_job_orm.list_by_document_version(job.document_id, job.version, lease.token)
                ready = _all_downloaded(jobs)
            if ready:
                advance(lease, DocumentTaskStage.MERGING, 80, "等待合并")
    except LeaseLost:
        return


def resume_parts(task, lease: TaskLease) -> bool:
    """领取后接管已提交分段；不重新解析已有远端任务。"""
    with lease.writes():
        jobs = parse_job_orm.adopt_task_jobs(task.id, task.document_id, task.version, lease.token)
    if not jobs:
        return False
    if _all_downloaded(jobs):
        advance(lease, DocumentTaskStage.MERGING, 80, "等待合并")
    return True


def run_merge(task, document, lease: TaskLease, reader: ObjectReader | None = None) -> None:
    """独立 MERGING 入口；发布解析指针与交接 CHUNKING 原子提交。"""
    with lease.writes():
        jobs = parse_job_orm.adopt_task_jobs(task.id, task.document_id, task.version, lease.token)
    if not _all_downloaded(jobs):
        advance(lease, DocumentTaskStage.PARSING, 5, "分段未齐，继续等待解析")
        return
    parts = [PartZip(item.part_index, item.page_start, item.page_end, item.result_key) for item in jobs]
    lease.ensure()
    stored = DocumentParsingService(reader, attempt_id=lease.token).collect(
        document.id, task.version, document.file_type, document.object_key, parts,
    )

    def publish() -> None:
        document_orm.set_parsed_artifacts(
            document.id, stored.md_key, stored.json_key, datetime.now(timezone.utc),
            expected_version=task.version,
        )
        # result_key 在此仍保留 ZIP，避免恢复时误把 manifest 当作压缩包。

    advance(lease, DocumentTaskStage.CHUNKING, 85, "等待切分", prepare=publish)


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


def _store_zip(job, mineru, reader, now: datetime, lease: TaskLease) -> bool:
    """下载并保存压缩包。失败时改下次轮询时间，返回是否可以继续合并。"""
    if not job.mineru_job_id:
        apply_plan(job, plan_lost(job, now), now, lease)
        return False
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = mineru.download(job.mineru_job_id, Path(directory))
            key = (
                f"documents/{job.document_id}/parse/{job.version}"
                f"/attempts/{lease.token}/parts/{job.part_index}/{uuid4().hex}.zip"
            )
            lease.ensure()
            reader.upload_file(key, path, "application/zip")
    except LeaseLost:
        raise
    except MinerUJobLost:
        apply_plan(job, plan_lost(job, now), now, lease)
        return False
    except ParserUnavailable as exc:
        _defer_download(job, exc.message, now, lease)
        return False
    except MinerURequestError as exc:
        apply_plan(job, plan_rejected(job, exc.message, now), now, lease)
        return False
    except Exception as exc:
        # 下载或对象存储故障走现有延迟重试。
        logger.exception("下载或保存分段失败: %s", job.id)
        _defer_download(job, str(exc) or "保存分段失败", now, lease)
        return False
    with lease.writes():
        parse_job_orm.save_zip(job.id, key, now)
    return True


def _all_downloaded(jobs: list[ParseJob]) -> bool:
    if not jobs:
        return False
    return all(
        item.status == ParseJobStatus.DOWNLOADING and bool(item.result_key)
        for item in jobs
    )


def _fail_document(
    job: ParseJob, message: str, now: datetime, lease: TaskLease
) -> None:
    with lease.writes():
        if task_orm.fail(job.task_id, message, now, job.lease_token):
            document_orm.mark_failed(
                job.document_id, message, now, expected_version=job.version
            )


def _defer_download(
    job: ParseJob, message: str, now: datetime, lease: TaskLease
) -> None:
    with lease.writes():
        parse_job_orm.set_status(
            job.id,
            ParseJobStatus.DOWNLOADING,
            now,
            now + _backoff(job.attempts),
            message,
        )


def _lease(job: ParseJob, parsing_only: bool = False) -> TaskLease:
    if not job.lease_token:
        # 升级前没有可靠执行标记的结果不能绑定到当前 worker。
        raise LeaseLost(job.task_id)
    if parsing_only:
        return TaskLease(job.task_id, job.lease_token, (DocumentTaskStage.PARSING,))
    return TaskLease(job.task_id, job.lease_token)


def _enqueue_download(job_id: str, token: str) -> None:
    from task.poll_parse import download_parse_result

    download_parse_result.delay(job_id, token)


def _enqueue_resubmit(job_id: str, token: str) -> None:
    from task.poll_parse import resubmit_parse_part

    resubmit_parse_part.delay(job_id, token)


def default_reader() -> StorageObjectReader:
    """Worker 使用的对象存储读取器。"""
    return StorageObjectReader()
