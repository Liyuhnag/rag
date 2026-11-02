# -*- coding: utf-8 -*-
"""父块回查的一级（进程内 LRU）与二级（Redis MGET/Pipeline）缓存实现。"""

import json
import logging
import time
from collections import OrderedDict
from collections.abc import Mapping
from collections.abc import Sequence
from threading import Lock

from services.retrieval.fusion.backfill.models import ParentChunkPayload

logger = logging.getLogger("rag.retrieval.backfill")

DEFAULT_L1_CAPACITY = 2000
DEFAULT_L1_TTL_SECONDS = 300.0
DEFAULT_L2_TTL_SECONDS = 3600
DEFAULT_REDIS_KEY_PREFIX = "rag:parent_chunk:"


class LocalParentCache:
    """L1：线程安全的进程内 LRU + TTL 父块缓存。"""

    def __init__(
        self,
        capacity: int = DEFAULT_L1_CAPACITY,
        ttl_seconds: float = DEFAULT_L1_TTL_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self.capacity = max(0, capacity)
        self.ttl_seconds = max(0.0, ttl_seconds)
        self._clock = clock
        self._lock = Lock()
        self._store: OrderedDict[str, tuple[float, ParentChunkPayload]] = OrderedDict()

    def get_many(
        self, parent_ids: Sequence[str]
    ) -> tuple[dict[str, ParentChunkPayload], list[str]]:
        """批量读取 L1 缓存，返回 (命中的字典, 未命中的 parent_id 列表)。"""
        if self.capacity <= 0 or self.ttl_seconds <= 0:
            return {}, list(parent_ids)

        hits: dict[str, ParentChunkPayload] = {}
        missed: list[str] = []
        now = self._clock()

        with self._lock:
            for pid in parent_ids:
                entry = self._store.get(pid)
                if entry is None:
                    missed.append(pid)
                    continue
                expires_at, payload = entry
                if now >= expires_at:
                    del self._store[pid]
                    missed.append(pid)
                    continue
                self._store.move_to_end(pid)
                hits[pid] = payload

        return hits, missed

    def put_many(self, items: Mapping[str, ParentChunkPayload]) -> None:
        """批量写入 L1 缓存，超出容量时按 LRU 淘汰最旧条目。"""
        if self.capacity <= 0 or self.ttl_seconds <= 0 or not items:
            return
        expires_at = self._clock() + self.ttl_seconds
        with self._lock:
            for pid, payload in items.items():
                if pid in self._store:
                    self._store.move_to_end(pid)
                self._store[pid] = (expires_at, payload)
            while len(self._store) > self.capacity:
                self._store.popitem(last=False)


class RedisParentCache:
    """L2：基于 Redis MGET / Pipeline 的分布式父块缓存（Redis 异常时自动静默降级）。"""

    def __init__(
        self,
        redis_url: str = "",
        client=None,
        *,
        ttl_seconds: int = DEFAULT_L2_TTL_SECONDS,
        key_prefix: str = DEFAULT_REDIS_KEY_PREFIX,
    ) -> None:
        self.redis_url = redis_url.strip()
        self._client = client
        self.ttl_seconds = max(1, ttl_seconds)
        self.key_prefix = key_prefix

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.redis_url:
            return None
        import redis

        self._client = redis.Redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.5,
        )
        return self._client

    def _key(self, parent_id: str) -> str:
        return f"{self.key_prefix}{parent_id}"

    def get_many(
        self, parent_ids: Sequence[str]
    ) -> tuple[dict[str, ParentChunkPayload], list[str]]:
        """使用单次 MGET 批量查询 Redis；Redis 不可用时全部计入 missed 交由 L3 MySQL 兜底。"""
        if not parent_ids:
            return {}, []
        try:
            client = self._get_client()
            if client is None:
                return {}, list(parent_ids)
            keys = [self._key(pid) for pid in parent_ids]
            raw_values = client.mget(keys)
        except Exception as exc:
            logger.warning("L2 Redis 父块缓存读取失败，降级直查 MySQL: %s", exc)
            return {}, list(parent_ids)

        hits: dict[str, ParentChunkPayload] = {}
        missed: list[str] = []
        for pid, raw in zip(parent_ids, raw_values, strict=True):
            if not raw:
                missed.append(pid)
                continue
            try:
                data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                hits[pid] = ParentChunkPayload.from_dict(data)
            except Exception:
                missed.append(pid)

        return hits, missed

    def put_many(self, items: Mapping[str, ParentChunkPayload]) -> None:
        """使用 Pipeline 批量写入 Redis 并设置过期时间。"""
        if not items:
            return
        try:
            client = self._get_client()
            if client is None:
                return
            pipe = client.pipeline(transaction=False)
            for pid, payload in items.items():
                serialized = json.dumps(payload.to_dict(), ensure_ascii=False)
                pipe.setex(self._key(pid), self.ttl_seconds, serialized)
            pipe.execute()
        except Exception as exc:
            logger.warning("L2 Redis 父块缓存回填失败（已忽略）: %s", exc)
