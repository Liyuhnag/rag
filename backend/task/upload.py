# -*- coding: utf-8 -*-
"""接收上传内容，生成异步处理任务。"""

from datetime import datetime
from datetime import timezone
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

from model.task import ProcessingTask
from model.task import orm as task_orm
from services.common.exceptions import BrokerUnavailable
from services.common.exceptions import QueueFull
from task.document import process_document

_QUEUE_NAME = "celery"
_QUEUE_TIMEOUT_SECONDS = 1


def accept_upload(
    name: str,
    file_type: str,
    content: bytes,
    queue_limit: int,
) -> ProcessingTask:
    """把文件落到临时路径并提交处理任务。"""
    _ensure_room(queue_limit)
    task_id = uuid4().hex
    now = datetime.now(timezone.utc)
    path = _temp_path(task_id)
    path.write_bytes(content)
    task = ProcessingTask(
        id=task_id,
        name=name,
        file_type=file_type,
        size_bytes=len(content),
        temp_path=str(path),
        status="PENDING",
        document_id=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    task_orm.insert(task)
    try:
        process_document.delay(task_id)
    except Exception as exc:
        current = task_orm.get(task_id)
        if current is None or current.status == "PENDING":
            task_orm.fail(task_id, str(exc) or "任务未能进入队列")
            path.unlink(missing_ok=True)
        raise
    stored = task_orm.get(task_id)
    return stored if stored is not None else task


def _ensure_room(queue_limit: int) -> None:
    """等待消息达到上限，或队列查不到时，拒绝新的上传。"""
    if _waiting_count() >= queue_limit:
        raise QueueFull("处理任务过多，请稍后再试")


def _waiting_count() -> int:
    """celery 列表里还没被 worker 取走的消息数。"""
    import redis
    from redis.exceptions import RedisError

    from config import load_settings

    client = redis.Redis.from_url(
        load_settings().redis_url,
        socket_connect_timeout=_QUEUE_TIMEOUT_SECONDS,
        socket_timeout=_QUEUE_TIMEOUT_SECONDS,
    )
    try:
        return int(client.llen(_QUEUE_NAME))
    except RedisError as exc:
        raise BrokerUnavailable("任务队列不可用") from exc
    finally:
        client.close()


def _temp_path(task_id: str) -> Path:
    directory = Path(gettempdir()) / "rag-uploads"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / task_id
