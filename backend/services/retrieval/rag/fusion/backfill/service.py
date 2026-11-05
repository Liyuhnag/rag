# -*- coding: utf-8 -*-
"""父块回填：权威状态校验、共享过滤、无 TTL 缓存及有界批量回源。"""

import time
from collections.abc import Mapping
from collections.abc import Sequence

from config import Settings
from services.retrieval.rag.fusion.backfill.cache import MySQLParentStore
from services.retrieval.rag.fusion.backfill.cache import ParentBloomFilter
from services.retrieval.rag.fusion.backfill.cache import RedisParentCache
from services.retrieval.rag.fusion.backfill.cache.connection import RedisConnection
from services.retrieval.rag.fusion.backfill.models import BackfillResult
from services.retrieval.rag.fusion.backfill.models import FusedParent
from services.retrieval.rag.fusion.backfill.models import ParentChunkRef
from services.retrieval.rag.fusion.models import FusedParentCandidate


class ParentBackfiller:
    """由应用进程复用；allowed_versions 必须由上层完成授权后传入。"""

    def __init__(
        self,
        redis_cache: RedisParentCache | None = None,
        mysql_store: MySQLParentStore | None = None,
        bloom_filter: ParentBloomFilter | None = None,
        *,
        settings: Settings | None = None,
        timeout_seconds: float = 3.0,
        lock_wait_seconds: float = 0.02,
        max_candidates: int = 200,
        sleep=time.sleep,
    ) -> None:
        settings = settings or Settings()
        guard = RedisConnection(settings.parent_guard_redis_url)
        self.redis = redis_cache or RedisParentCache(
            settings.parent_cache_redis_url, guard_connection=guard
        )
        self.bloom = bloom_filter or ParentBloomFilter(connection=self.redis.guard)
        self.mysql = mysql_store or MySQLParentStore()
        self.timeout_seconds = max(0.05, timeout_seconds)
        if self.redis.lock_ttl_ms / 1000 <= self.timeout_seconds:
            raise ValueError("父块锁租期必须大于单次回填预算")
        self.lock_wait_seconds = max(0.001, lock_wait_seconds)
        self.max_candidates = max(1, max_candidates)
        self._sleep = sleep

    def backfill(
        self,
        candidates: Sequence[FusedParentCandidate],
        *,
        allowed_versions: Mapping[str, int],
        tenant_id: str = "default",
    ) -> BackfillResult:
        """先授权范围与当前状态校验，正文缓存命中不能绕过此边界。"""
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        if not tenant_id.strip() or len(candidates) > self.max_candidates:
            raise ValueError("租户作用域不能为空，候选数不能超过回填上限")
        if not candidates or not allowed_versions:
            return BackfillResult(missing_parent_count=len(candidates))
        selected = {}
        conflicted = set()
        for item in candidates:
            if (
                not item.parent_id
                or allowed_versions.get(item.document_id) != item.version
            ):
                continue
            existing = selected.get(item.parent_id)
            if existing and (existing.document_id, existing.version) != (
                item.document_id,
                item.version,
            ):
                conflicted.add(item.parent_id)
            else:
                selected.setdefault(item.parent_id, item)
        for pid in conflicted:
            selected.pop(pid, None)
        documents = self.mysql.get_documents(
            [item.document_id for item in selected.values()], deadline=deadline
        )
        refs = []
        for item in selected.values():
            document = documents.get(item.document_id)
            if document is not None and document.version == item.version:
                refs.append(
                    ParentChunkRef(
                        item.parent_id,
                        item.document_id,
                        item.version,
                        tenant_id,
                        document.revision,
                    )
                )
        refs = self.bloom.filter_many(
            refs,
            {key: value.parent_count for key, value in documents.items()},
            self.mysql.get_snapshot_ids,
            deadline=deadline,
        )
        resolved, missed = self.redis.get_many(refs, deadline=deadline)
        redis_hits = len(resolved)
        mysql_hits = 0
        while missed:
            tickets, waiting = self.redis.acquire_locks(missed, deadline=deadline)
            try:
                # 无论第几次获得锁，都必须重查；只有校验成功的命中才从回源集合移除。
                recovered, to_load = self.redis.get_many(
                    list(tickets), deadline=deadline
                )
                redis_hits += len(recovered)
                resolved.update(recovered)
                if to_load:
                    loaded = self.mysql.get_many(
                        [ref.parent_id for ref in to_load], deadline=deadline
                    )
                    valid = {}
                    for ref in to_load:
                        payload = loaded.get(ref.parent_id)
                        if (
                            payload
                            and payload.document_id == ref.document_id
                            and payload.version == ref.version
                        ):
                            valid[ref] = payload
                            resolved[ref.parent_id] = payload
                    mysql_hits += len(valid)
                    self.redis.put_many(valid, deadline=deadline)
            finally:
                self.redis.release_locks(tickets)
            if not waiting:
                break
            recovered, missed = self.redis.get_many(waiting, deadline=deadline)
            redis_hits += len(recovered)
            resolved.update(recovered)
            if missed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("父块回填等待已超时")
                self._sleep(min(self.lock_wait_seconds, remaining))

        # 回填期间可能发生删除或版本切换，返回前再次读取权威状态。
        current = self.mysql.get_documents(
            [payload.document_id for payload in resolved.values()], deadline=deadline
        )
        parents = []
        for ref in refs:
            payload = resolved.get(ref.parent_id)
            document = current.get(ref.document_id)
            if (
                payload is None
                or document is None
                or document.version != ref.version
                or document.revision != ref.revision
            ):
                continue
            item = selected[ref.parent_id]
            parents.append(
                FusedParent(
                    parent_id=payload.parent_id,
                    document_id=payload.document_id,
                    document_name=document.name,
                    version=payload.version,
                    parent_index=payload.parent_index,
                    file_type=payload.file_type,
                    content=payload.content,
                    heading_path=payload.heading_path,
                    locator=payload.locator,
                    page_start=payload.page_start,
                    page_end=payload.page_end,
                    token_count=payload.token_count,
                    score=item.rrf_score,
                    rrf_score=item.rrf_score,
                    rerank_score=None,
                    rank=len(parents) + 1,
                    matched_child_count=item.matched_child_count,
                    matched_children=item.matched_children,
                )
            )
        if time.monotonic() >= deadline:
            raise TimeoutError("父块回填已超时")
        return BackfillResult(
            parents=tuple(parents),
            missing_parent_count=len(selected) - len(parents),
            redis_hits=redis_hits,
            mysql_hits=mysql_hits,
            backfill_ms=max(0, int((time.monotonic() - started) * 1000)),
        )

    def close(self) -> None:
        self.redis.close()
        if self.bloom.connection is not self.redis.guard:
            self.bloom.connection.close()
