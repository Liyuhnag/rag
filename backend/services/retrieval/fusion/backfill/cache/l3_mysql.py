# -*- coding: utf-8 -*-
"""L3 三级存储：从 MySQL 批量联表回源父块，内置信号量并发控制防止雪崩压垮数据库。"""

from collections.abc import Callable
from collections.abc import Sequence
from threading import BoundedSemaphore

from model.document_chunk import orm as chunk_orm
from services.retrieval.fusion.backfill.models import ParentChunkPayload

DEFAULT_MYSQL_MAX_CONCURRENCY = 16
DEFAULT_MYSQL_ACQUIRE_TIMEOUT_SECONDS = 3.0


class MySQLParentStore:
    """L3：按 parent_id 列表从 MySQL 批量查询父块，并使用有界信号量限制最大并发。"""

    def __init__(
        self,
        db_loader: Callable[[Sequence[str]], list[dict[str, object]]] | None = None,
        *,
        max_concurrency: int = DEFAULT_MYSQL_MAX_CONCURRENCY,
        acquire_timeout_seconds: float = DEFAULT_MYSQL_ACQUIRE_TIMEOUT_SECONDS,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency 必须 >= 1")
        self._load_rows = db_loader or chunk_orm.list_parents_by_ids
        self.max_concurrency = max_concurrency
        self.acquire_timeout_seconds = max(0.05, acquire_timeout_seconds)
        self._semaphore = BoundedSemaphore(max_concurrency)

    def get_many(self, parent_ids: Sequence[str]) -> dict[str, ParentChunkPayload]:
        """在并发信号量保护下从 MySQL 批量查询父块。"""
        if not parent_ids:
            return {}
        acquired = self._semaphore.acquire(timeout=self.acquire_timeout_seconds)
        if not acquired:
            raise RuntimeError(
                f"MySQL 父块回查并发已达上限 ({self.max_concurrency})，触发限流保护"
            )
        try:
            rows = self._load_rows(parent_ids)
        finally:
            self._semaphore.release()

        results: dict[str, ParentChunkPayload] = {}
        for row in rows:
            payload = _row_to_payload(row)
            results[payload.parent_id] = payload
        return results


def _row_to_payload(row: dict[str, object]) -> ParentChunkPayload:
    page_start = row.get("page_start")
    page_end = row.get("page_end")
    return ParentChunkPayload(
        parent_id=str(row["id"]),
        document_id=str(row["document_id"]),
        document_name=str(row.get("document_name", "")),
        version=int(row.get("version", 1)),
        parent_index=int(row.get("parent_index") or 0),
        file_type=str(row.get("file_type", "")),
        content=str(row.get("content", "")),
        heading_path=str(row.get("heading_path", "")),
        locator=str(row.get("locator", "")),
        page_start=None if page_start is None else int(page_start),
        page_end=None if page_end is None else int(page_end),
        token_count=int(row.get("token_count") or 0),
    )
