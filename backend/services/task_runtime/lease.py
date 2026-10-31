# -*- coding: utf-8 -*-
"""解析、合并和切分共用的续租与写入门禁。"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from threading import Event
from threading import RLock
from threading import Thread

from model.document_task import orm as task_orm
from model.document_task.lease import LeaseLost
from model.document_task.model import DocumentTaskStage

logger = logging.getLogger(__name__)
PARSE_STAGES = (
    DocumentTaskStage.PARSING,
    DocumentTaskStage.MERGING,
    DocumentTaskStage.CHUNKING,
)


class TaskLease:
    """只使用领取时的 token；续租失败后不再允许任何业务写入。"""

    def __init__(
        self,
        task_id: str,
        token: str,
        stages: tuple[DocumentTaskStage, ...] = PARSE_STAGES,
        interval: float = 120,
    ) -> None:
        if not token or not stages or interval <= 0:
            raise ValueError("租约需要 token、允许阶段和正数续租间隔")
        self.task_id = task_id
        self.token = token
        self.stages = stages
        self.interval = interval
        self._stopped = Event()
        self._lost = Event()
        self._writing = RLock()
        self._thread: Thread | None = None

    def __enter__(self) -> "TaskLease":
        self._renew()
        self._check()
        self._thread = Thread(target=self._heartbeat, name="parse-lease", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _check(self) -> None:
        if self._lost.is_set():
            raise LeaseLost(self.task_id)

    def ensure(self) -> None:
        """外部调用前确认执行权；不能用数据库中的新 token 替换本次 token。"""
        self._check()
        if not task_orm.owns_lease(self.task_id, self.token, self.stages):
            self._lost.set()
            raise LeaseLost(self.task_id)

    @contextmanager
    def writes(self) -> Iterator[None]:
        """业务写入与租约校验同一事务，恢复者在提交前无法换掉 token。"""
        with self._writing:
            self._check()
            with task_orm.lease_guard(self.task_id, self.token, self.stages):
                yield

    def _renew(self) -> None:
        # 业务事务持有任务行锁时，恢复无法抢占，提交前会刷新时间；无需自锁续租。
        if not self._writing.acquire(blocking=False):
            return
        try:
            if not task_orm.renew(
                self.task_id, self.token, datetime.now(timezone.utc), self.stages
            ):
                self._lost.set()
        except Exception:
            # 心跳线程无法把数据库异常交回调用栈，锁存失效供下一次写入检查。
            logger.exception("任务续租失败: %s", self.task_id)
            self._lost.set()
        finally:
            self._writing.release()

    def _heartbeat(self) -> None:
        while not self._stopped.wait(self.interval):
            self._renew()
            if self._lost.is_set():
                return
