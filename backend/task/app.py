# -*- coding: utf-8 -*-
"""Celery 应用。任务模块和进程入口共用这一份定义。"""

import os

from celery import Celery
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


@worker_process_init.connect
def _open_storage(**_kwargs: object) -> None:
    """每个 worker 进程自己连接数据库和对象存储。"""
    from services.common.connect import connect_storage

    connect_storage(load_settings())
