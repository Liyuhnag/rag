# -*- coding: utf-8 -*-
"""独立索引队列：MySQL 子块 → embedding → Milvus + Elasticsearch。"""

import logging
import threading
from datetime import datetime
from datetime import timezone

from model.document import orm as document_orm
from model.document_task import orm as task_orm
from model.document_task.model import DocumentTaskStage
from model.document_task.model import DocumentTaskStatus
from services.indexing.service import DocumentIndexingService
from task.app import celery_app

logger = logging.getLogger("rag.task")
_STALE_VERSION = "文档版本已变化，索引任务已过期"
_LEASE_RENEW_SECONDS = 120


@celery_app.task(
    name="rag.index_document",
    ignore_result=True,
    queue="index",
    soft_time_limit=1740,
    time_limit=1800,
)
def index_document(task_id: str) -> None:
    """消费已领取任务。看门狗续租；失联后由维护任务换新标记再补投。"""
    task = task_orm.get(task_id)
    if (
        task is None
        or task.status != DocumentTaskStatus.RUNNING
        or task.stage != DocumentTaskStage.INDEXING
    ):
        return
    token = task.lease_token or task_orm.install_index_lease(
        task_id, datetime.now(timezone.utc)
    )
    if not token:
        return
    document = document_orm.get(task.document_id)
    if document is None:
        task_orm.fail(task_id, "文档不存在", datetime.now(timezone.utc), token)
        return
    if document.parse_version != task.version:
        _fail_stale_version(task_id, token)
        return

    stop = threading.Event()
    renewer = threading.Thread(
        target=_renew_lease,
        args=(stop, task_id, token),
        name="index-lease",
        daemon=True,
    )
    renewer.start()
    try:
        task_orm.touch_indexing(task_id, token, datetime.now(timezone.utc))

        def heartbeat(completed: int, total: int, last_chunk_id: str):
            task_orm.update_index_progress(
                task_id,
                completed,
                total,
                last_chunk_id,
                token,
                datetime.now(timezone.utc),
            )

        def lease():
            task_orm.touch_indexing(task_id, token, datetime.now(timezone.utc))

        try:
            if DocumentIndexingService().run(
                document, task.version, heartbeat=heartbeat, lease=lease
            ):
                if not task_orm.complete(
                    task_id, "解析与索引完成", datetime.now(timezone.utc), token
                ):
                    logger.error("索引已发布，但任务完成标记未写上: %s", task_id)
            else:
                # 加锁后版本已变，外部索引未写入。只结束这条旧任务。
                _fail_stale_version(task_id, token)
        except Exception as exc:
            # 外部存储边界记录失败，保留 MySQL 子块供重新索引。
            # 标记已被新领取换掉时，不再改任务和文档。
            logger.exception("文档索引失败: %s", task_id)
            now = datetime.now(timezone.utc)
            message = str(exc) or "索引失败"
            if task_orm.fail(task_id, message, now, token):
                document_orm.mark_failed(
                    document.id, message, now, expected_version=task.version
                )
                raise
            logger.error("索引任务已由新的领取接管: %s", task_id)
    finally:
        stop.set()
        renewer.join(timeout=5)


def _renew_lease(stop: threading.Event, task_id: str, lease_token: str) -> None:
    """任务还活着时按固定间隔续租。进程退出后线程停止，租约不再延长。"""
    while not stop.wait(_LEASE_RENEW_SECONDS):
        try:
            task_orm.touch_indexing(
                task_id, lease_token, datetime.now(timezone.utc)
            )
        except Exception:
            # 续租线程不能因一次数据库故障退出，否则长请求会在租约到期后被另一次领取接走。
            logger.exception("索引租约续期失败: %s", task_id)


def _fail_stale_version(task_id: str, lease_token: str) -> None:
    """旧版本不再索引。不修改文档，避免把新版本标成失败。"""
    logger.error("索引任务版本已过期: %s", task_id)
    task_orm.fail(task_id, _STALE_VERSION, datetime.now(timezone.utc), lease_token)
