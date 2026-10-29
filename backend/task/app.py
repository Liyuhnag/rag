# -*- coding: utf-8 -*-
"""Celery 应用。任务模块和进程入口共用这一份定义。"""

import os

from celery import Celery
from celery import current_app
from celery.app import trace
from celery.signals import worker_process_init
from dotenv import load_dotenv

from config import ENV_FILE
from config import load_settings

load_dotenv(ENV_FILE, override=False)


def _result_backend(broker: str) -> str:
    if broker.endswith("/0"):
        return f"{broker[:-1]}1"
    return broker


def _broker_url() -> str:
    """broker 只依赖 REDIS_URL，不要求完整的环境变量。"""
    return os.getenv("REDIS_URL", "").strip() or "redis://127.0.0.1:6379/0"


_redis_url = _broker_url()
celery_app = Celery(
    "rag",
    broker=_redis_url,
    backend=_result_backend(_redis_url),
    include=["task"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=4,
    worker_send_task_events=True,
    task_send_sent_event=True,
    broker_connection_retry_on_startup=True,
)
celery_app.conf.beat_schedule = {
    "expire-uploads": {
        "task": "rag.expire_uploads",
        "schedule": 3600.0,
    },
    "retry-aborting-uploads": {
        "task": "rag.retry_aborting_uploads",
        "schedule": 3600.0,
    },
    "recover-completing-uploads": {
        "task": "rag.recover_completing_uploads",
        "schedule": 300.0,
    },
    "cleanup-orphan-mpu": {
        "task": "rag.cleanup_orphan_mpu",
        "schedule": 86400.0,
    },
    "cleanup-orphan-object": {
        "task": "rag.cleanup_orphan_object",
        "schedule": 86400.0,
    },
    "cleanup-old-sessions": {
        "task": "rag.cleanup_old_sessions",
        "schedule": 86400.0,
    },
    "redispatch-pending-tasks": {
        "task": "rag.redispatch_pending_tasks",
        "schedule": 300.0,
    },
    "poll-parse-jobs": {
        "task": "rag.poll_parse_jobs",
        "schedule": 10.0,
    },
}


@worker_process_init.connect
def _open_storage(**_kwargs: object) -> None:
    """每个 worker 进程自己连接数据库和对象存储。"""
    if not trace._localized:
        trace.setup_worker_optimizations(current_app._get_current_object())

    from services.common.connect import connect_storage

    connect_storage(load_settings())
