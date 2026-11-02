# -*- coding: utf-8 -*-
"""父块回源：批量查询、文档状态核验及进程共享并发预算。"""

import time
from collections.abc import Sequence
from threading import BoundedSemaphore
from threading import Lock

from model.document_chunk import orm as chunk_orm
from services.retrieval.fusion.backfill.models import ParentChunkPayload
from services.retrieval.fusion.backfill.models import ParentDocument

DEFAULT_MYSQL_MAX_CONCURRENCY = 16
_LIMITERS = {}
_LIMITERS_LOCK = Lock()


def _shared_limiter(limit: int) -> BoundedSemaphore:
    with _LIMITERS_LOCK:
        return _LIMITERS.setdefault(limit, BoundedSemaphore(limit))


class MySQLParentStore:
    """所有读取共享进程内预算，避免每个请求新建实例绕过限流。"""

    def __init__(
        self,
        db_loader=None,
        *,
        document_loader=None,
        snapshot_loader=None,
        max_concurrency: int = DEFAULT_MYSQL_MAX_CONCURRENCY,
        acquire_timeout_seconds: float = 0.2,
        query_timeout_seconds: float = 1.0,
        batch_size: int = 100,
    ) -> None:
        if min(max_concurrency, batch_size) < 1:
            raise ValueError("并发数和批次大小必须大于 0")
        self._load_rows = db_loader or chunk_orm.list_parents_by_ids
        self._load_documents = document_loader or chunk_orm.list_ready_parent_documents
        self._load_ids = snapshot_loader or chunk_orm.list_parent_ids_for_snapshot
        self.max_concurrency = max_concurrency
        self.batch_size = batch_size
        self.acquire_timeout_seconds = max(0.01, acquire_timeout_seconds)
        self.query_timeout_seconds = max(0.01, query_timeout_seconds)
        self._semaphore = _shared_limiter(max_concurrency)

    def _query(self, loader, *args, deadline=None):
        remaining = self.query_timeout_seconds
        if deadline is not None:
            remaining = min(remaining, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("父块回源已超时")
        if not self._semaphore.acquire(
            timeout=min(remaining, self.acquire_timeout_seconds)
        ):
            raise RuntimeError(
                f"MySQL 父块回查并发已达上限 ({self.max_concurrency})，触发限流保护"
            )
        try:
            if deadline is not None:
                remaining = min(self.query_timeout_seconds, deadline - time.monotonic())
            if remaining <= 0:
                raise TimeoutError("父块回源已超时")
            rows = loader(*args, timeout_seconds=remaining)
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("父块回源已超时")
            return rows
        finally:
            self._semaphore.release()

    def get_many(
        self, parent_ids: Sequence[str], *, deadline: float | None = None
    ) -> dict[str, ParentChunkPayload]:
        results = {}
        clean = list(dict.fromkeys(parent_ids))
        for start in range(0, len(clean), self.batch_size):
            batch = clean[start : start + self.batch_size]
            rows = self._query(self._load_rows, batch, deadline=deadline)
            for row in rows:
                if str(row["id"]) in batch:
                    payload = _row_to_payload(row)
                    if payload.content.strip():
                        results[payload.parent_id] = payload
        return results

    def get_documents(
        self, document_ids, *, deadline=None
    ) -> dict[str, ParentDocument]:
        results = {}
        clean = list(dict.fromkeys(document_ids))
        for start in range(0, len(clean), self.batch_size):
            rows = self._query(
                self._load_documents,
                clean[start : start + self.batch_size],
                deadline=deadline,
            )
            for row in rows:
                document = ParentDocument(
                    document_id=str(row["id"]),
                    version=int(row["parse_version"]),
                    parent_count=int(row["parent_chunk_count"]),
                    name=str(row["name"]),
                    revision=row["updated_at"].strftime("%Y-%m-%d %H:%M:%S.%f"),
                )
                results[document.document_id] = document
        return results

    def get_snapshot_ids(
        self, document_id, version, count, revision, *, deadline=None
    ) -> list[str]:
        rows = self._query(
            self._load_ids, document_id, version, count, revision, deadline=deadline
        )
        return [str(row["id"]) for row in rows]


def _row_to_payload(row: dict[str, object]) -> ParentChunkPayload:
    page_start = row.get("page_start")
    page_end = row.get("page_end")
    return ParentChunkPayload(
        parent_id=str(row["id"]),
        document_id=str(row["document_id"]),
        document_name=str(row.get("document_name", "")),
        version=int(row["version"]),
        parent_index=int(row.get("parent_index") or 0),
        file_type=str(row.get("file_type", "")),
        content=str(row.get("content", "")),
        heading_path=str(row.get("heading_path", "")),
        locator=str(row.get("locator", "")),
        page_start=None if page_start is None else int(page_start),
        page_end=None if page_end is None else int(page_end),
        token_count=int(row.get("token_count") or 0),
    )
