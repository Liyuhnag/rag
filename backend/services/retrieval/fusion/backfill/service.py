# -*- coding: utf-8 -*-
"""父块回查服务（ParentBackfiller）。

采用三级架构解决缓存三大问题：
1. 第一级（bloom.py - 防缓存穿透）：
   - 使用固定内存的 ParentBloomFilter（位数组布隆过滤器）拦截不存在的 parent_id，
     无需缓存空值，避免海量无效 ID 撑爆内存与 Redis。
2. 第二级（redis_cache.py - 防缓存击穿 & 雪崩）：
   - 使用 Redis MGET 批量读取父块；写入时每个 key 独立附加随机 TTL 抖动（Jitter）；
   - 未命中需回源时，通过 Redis 分布式互斥锁（SET NX PX + Lua 解锁）控制并发，
     未抢到锁的进程短暂等待后重试读取 Redis，复用持锁进程刚回填的结果。
3. 第三级（mysql_store.py - 防缓存雪崩）：
   - 使用 MySQLParentStore 批量联表查询，内置 BoundedSemaphore 限制最大并发数。
"""

import logging
import time
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Sequence

from services.retrieval.fusion.backfill.cache import MySQLParentStore
from services.retrieval.fusion.backfill.cache import ParentBloomFilter
from services.retrieval.fusion.backfill.cache import RedisParentCache
from services.retrieval.fusion.backfill.models import BackfillResult
from services.retrieval.fusion.backfill.models import FusedParent
from services.retrieval.fusion.backfill.models import ParentChunkPayload
from services.retrieval.fusion.models import FusedParentCandidate

logger = logging.getLogger("rag.retrieval.backfill")

DEFAULT_LOCK_WAIT_RETRIES = 3
DEFAULT_LOCK_WAIT_SECONDS = 0.02


class ParentBackfiller:
    """按 布隆过滤器 -> Redis 分布式缓存 -> MySQL 回查父块正文并保持 RRF 排名顺序。"""

    def __init__(
        self,
        redis_cache: RedisParentCache | None = None,
        mysql_store: MySQLParentStore | None = None,
        bloom_filter: ParentBloomFilter | None = None,
        db_loader: Callable[[Sequence[str]], list[dict[str, object]]] | None = None,
        *,
        lock_wait_retries: int = DEFAULT_LOCK_WAIT_RETRIES,
        lock_wait_seconds: float = DEFAULT_LOCK_WAIT_SECONDS,
        sleep=time.sleep,
    ) -> None:
        self.redis = redis_cache if redis_cache is not None else RedisParentCache()
        self.mysql = (
            mysql_store
            if mysql_store is not None
            else MySQLParentStore(db_loader=db_loader)
        )
        self.bloom = (
            bloom_filter if bloom_filter is not None else ParentBloomFilter()
        )
        self.lock_wait_retries = max(1, lock_wait_retries)
        self.lock_wait_seconds = max(0.001, lock_wait_seconds)
        self._sleep = sleep

    def register_valid_parents(self, parent_ids: Iterable[str]) -> None:
        """将已知存在的父块 ID 集合预热注册进布隆过滤器，开启防穿透拦截。"""
        self.bloom.add_many(parent_ids)

    def backfill(
        self,
        candidates: Sequence[FusedParentCandidate],
        *,
        allowed_versions: dict[str, int] | None = None,
    ) -> BackfillResult:
        """按 布隆过滤器 -> Redis -> MySQL 回查父块，严格按 candidates 的 RRF 顺序组装 FusedParent。"""
        started = time.monotonic()
        if not candidates:
            return BackfillResult()

        # ==================== 第一级：布隆过滤器防穿透拦截 ====================
        ordered_ids: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            pid = item.parent_id
            if not pid or pid in seen:
                continue
            seen.add(pid)
            if not self.bloom.might_contain(pid):
                logger.debug("布隆过滤器拦截不存在的父块 ID: %s", pid)
                continue
            ordered_ids.append(pid)

        resolved: dict[str, ParentChunkPayload] = {}

        # ==================== 第二级：Redis 分布式缓存批量查询 ====================
        redis_hit_count = 0
        mysql_lookup_ids: list[str] = []
        if ordered_ids:
            redis_found, redis_missed = self.redis.get_many(ordered_ids)
            for pid, payload in redis_found.items():
                if _is_version_valid(payload, allowed_versions):
                    resolved[pid] = payload
                    redis_hit_count += 1
                else:
                    mysql_lookup_ids.append(pid)
            mysql_lookup_ids.extend(redis_missed)

        # ==================== 第三级：分布式锁防击穿 + MySQL 批量回源 ====================
        mysql_hit_count = 0
        if mysql_lookup_ids:
            redis_recovered, mysql_loaded = self._load_with_distributed_lock(
                mysql_lookup_ids, allowed_versions
            )
            if redis_recovered:
                resolved.update(redis_recovered)
                redis_hit_count += len(redis_recovered)
            if mysql_loaded:
                resolved.update(mysql_loaded)
                mysql_hit_count += len(mysql_loaded)

        # ==================== 按 RRF 候选顺序组装最终父块列表 ====================
        parents: list[FusedParent] = []
        missing_count = 0
        for candidate in candidates:
            payload = resolved.get(candidate.parent_id)
            if payload is None:
                missing_count += 1
                logger.warning(
                    "父块回查缺失或已拦截: parent_id=%s document_id=%s",
                    candidate.parent_id,
                    candidate.document_id,
                )
                continue
            if payload.document_id != candidate.document_id:
                missing_count += 1
                logger.warning(
                    "父块回查 document_id 不一致: parent_id=%s (%s vs %s)",
                    candidate.parent_id,
                    payload.document_id,
                    candidate.document_id,
                )
                continue

            parents.append(
                FusedParent(
                    parent_id=payload.parent_id,
                    document_id=payload.document_id,
                    document_name=payload.document_name,
                    version=payload.version,
                    parent_index=payload.parent_index,
                    file_type=payload.file_type,
                    content=payload.content,
                    heading_path=payload.heading_path or candidate.heading_path,
                    locator=payload.locator or candidate.locator,
                    page_start=(
                        payload.page_start
                        if payload.page_start is not None
                        else candidate.page_start
                    ),
                    page_end=(
                        payload.page_end
                        if payload.page_end is not None
                        else candidate.page_end
                    ),
                    token_count=payload.token_count,
                    score=candidate.rrf_score,
                    rrf_score=candidate.rrf_score,
                    rerank_score=None,
                    rank=len(parents) + 1,
                    matched_child_count=candidate.matched_child_count,
                    matched_children=candidate.matched_children,
                )
            )

        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        return BackfillResult(
            parents=tuple(parents),
            missing_parent_count=missing_count,
            redis_hits=redis_hit_count,
            mysql_hits=mysql_hit_count,
            backfill_ms=elapsed_ms,
        )

    def _load_with_distributed_lock(
        self,
        lookup_ids: list[str],
        allowed_versions: dict[str, int] | None,
    ) -> tuple[dict[str, ParentChunkPayload], dict[str, ParentChunkPayload]]:
        """使用 Redis 分布式互斥锁控制并发回源，防止热点过期瞬间击穿 MySQL。"""
        remaining = list(lookup_ids)
        redis_recovered: dict[str, ParentChunkPayload] = {}

        for attempt in range(self.lock_wait_retries):
            lock_ticket = self.redis.acquire_lock(remaining)
            if lock_ticket is not None:
                lock_key, token = lock_ticket
                try:
                    if attempt > 0:
                        found, still_missed = self.redis.get_many(remaining)
                        for pid, payload in found.items():
                            if _is_version_valid(payload, allowed_versions):
                                redis_recovered[pid] = payload
                        remaining = [
                            pid for pid in still_missed if pid not in redis_recovered
                        ]
                        if not remaining:
                            return redis_recovered, {}
                    mysql_loaded = self._query_mysql_and_populate(
                        remaining, allowed_versions
                    )
                    return redis_recovered, mysql_loaded
                finally:
                    self.redis.release_lock(lock_key, token)

            # 未抢到锁：等待持锁进程回填 Redis 后重查
            self._sleep(self.lock_wait_seconds)
            found, still_missed = self.redis.get_many(remaining)
            for pid, payload in found.items():
                if _is_version_valid(payload, allowed_versions):
                    redis_recovered[pid] = payload
            remaining = [pid for pid in still_missed if pid not in redis_recovered]
            if not remaining:
                return redis_recovered, {}

        mysql_loaded = self._query_mysql_and_populate(remaining, allowed_versions)
        return redis_recovered, mysql_loaded

    def _query_mysql_and_populate(
        self,
        parent_ids: Sequence[str],
        allowed_versions: dict[str, int] | None,
    ) -> dict[str, ParentChunkPayload]:
        if not parent_ids:
            return {}
        mysql_found = self.mysql.get_many(parent_ids)
        mysql_valid = {
            pid: payload
            for pid, payload in mysql_found.items()
            if _is_version_valid(payload, allowed_versions)
        }
        if mysql_valid:
            if self.bloom.is_ready:
                self.bloom.add_many(mysql_valid.keys())
            self.redis.put_many(mysql_valid)
        return mysql_valid


def _is_version_valid(
    payload: ParentChunkPayload,
    allowed_versions: dict[str, int] | None,
) -> bool:
    if allowed_versions is None:
        return True
    expected = allowed_versions.get(payload.document_id)
    if expected is None:
        return False
    return payload.version == expected
