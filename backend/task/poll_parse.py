# -*- coding: utf-8 -*-
"""MinerU 分段轮询任务。查询放在维护队列，下载和重试放在解析队列。"""

from datetime import datetime
from datetime import timezone

from services.parsing.mineru.client import MinerUClient
from services.parsing.poll import default_reader
from services.parsing.poll import download_job
from services.parsing.poll import poll_due
from services.parsing.poll import resubmit_job
from task.app import celery_app


@celery_app.task(
    name="rag.poll_parse_jobs",
    ignore_result=True,
    queue="maintenance",
)
def poll_parse_jobs() -> None:
    """每 10 秒领取到期分段，查询 MinerU 状态。"""
    poll_due(datetime.now(timezone.utc))


@celery_app.task(
    name="rag.download_parse_result",
    ignore_result=True,
    soft_time_limit=300,
    time_limit=330,
)
def download_parse_result(job_id: str) -> None:
    """下载分段结果。全部分段到齐后合并。"""
    download_job(
        job_id,
        MinerUClient(),
        default_reader(),
        datetime.now(timezone.utc),
    )


@celery_app.task(
    name="rag.resubmit_parse_part",
    ignore_result=True,
    soft_time_limit=300,
    time_limit=330,
)
def resubmit_parse_part(job_id: str) -> None:
    """MinerU 任务丢失或失败时，用原分段再提交一次。"""
    resubmit_job(
        job_id,
        MinerUClient(),
        default_reader(),
        datetime.now(timezone.utc),
    )
