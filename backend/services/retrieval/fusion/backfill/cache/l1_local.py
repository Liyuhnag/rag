# -*- coding: utf-8 -*-
"""L1 一级缓存：线程安全的进程内 LRU + TTL 父块缓存。"""

import time
from collections import OrderedDict
from collections.abc import Mapping
from collections.abc import Sequence
from threading import Lock

from services.retrieval.fusion.backfill.models import ParentChunkPayload

DEFAULT_L1_CAPACITY = 2000
DEFAULT_L1_TTL_SECONDS = 300.0


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
