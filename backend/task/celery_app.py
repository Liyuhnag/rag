# -*- coding: utf-8 -*-
"""Celery 应用。任务状态在 MySQL，Redis 只做队列。"""

from celery import Celery
from celery.signals import worker_process_init

from config import load_settings


def _result_backend(broker: str) -> str:
    if broker.endswith("/0"):
        return f"{broker[:-1]}1"
    return broker


_settings = load_settings()
celery_app = Celery(
    "rag",
    broker=_settings.redis_url,
    backend=_result_backend(_settings.redis_url),
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
