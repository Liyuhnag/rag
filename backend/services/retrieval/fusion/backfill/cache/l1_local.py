# -*- coding: utf-8 -*-
"""L1 一级缓存与防穿透布隆过滤器（Bloom Filter + 进程内 LRU/TTL 抖动缓存）。"""

import hashlib
import random
import time
from collections import OrderedDict
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from threading import Lock

from services.retrieval.fusion.backfill.models import ParentChunkPayload

DEFAULT_L1_CAPACITY = 2000
DEFAULT_L1_TTL_SECONDS = 300.0
DEFAULT_L1_JITTER_SECONDS = 60.0
DEFAULT_BLOOM_BIT_SIZE = 1 << 20  # 1M bits (~128KB 固定内存)，绝不随无效请求膨胀
DEFAULT_BLOOM_HASH_COUNT = 5


class ParentBloomFilter:
    """防缓存穿透：固定内存位数组布隆过滤器。

    用于拦截根本不存在的 parent_id，避免缓存空值导致内存被海量无效 key 占满。
    - might_contain(id) == False -> 100% 确定该父块不存在，直接拦截；
    - might_contain(id) == True  -> 可能存在，放行进入缓存与数据库查询。
    """

    def __init__(
        self,
        bit_size: int = DEFAULT_BLOOM_BIT_SIZE,
        hash_count: int = DEFAULT_BLOOM_HASH_COUNT,
    ) -> None:
        if bit_size < 64 or hash_count < 1:
            raise ValueError("bit_size 必须 >= 64 且 hash_count >= 1")
        self.bit_size = bit_size
        self.hash_count = hash_count
        self._bytes = bytearray((bit_size + 7) // 8)
        self._lock = Lock()
        self._count = 0

    @property
    def is_ready(self) -> bool:
        """是否已写入过有效 parent_id；未预热时不拦截。"""
        return self._count > 0

    def add(self, parent_id: str) -> None:
        """将单个有效 parent_id 加入布隆过滤器。"""
        key = parent_id.strip()
        if not key:
            return
        positions = self._positions(key)
        with self._lock:
            for pos in positions:
                self._bytes[pos >> 3] |= 1 << (pos & 7)
            self._count += 1

    def add_many(self, parent_ids: Iterable[str]) -> None:
        """批量将有效 parent_id 加入布隆过滤器。"""
        batch_positions: list[int] = []
        added = 0
        for raw in parent_ids:
            key = raw.strip()
            if key:
                batch_positions.extend(self._positions(key))
                added += 1
        if not added:
            return
        with self._lock:
            for pos in batch_positions:
                self._bytes[pos >> 3] |= 1 << (pos & 7)
            self._count += added

    def might_contain(self, parent_id: str) -> bool:
        """判断 parent_id 是否可能存在；若过滤器尚未加载任何数据则默认放行。"""
        if not self.is_ready:
            return True
        key = parent_id.strip()
        if not key:
            return False
        positions = self._positions(key)
        with self._lock:
            for pos in positions:
                if not (self._bytes[pos >> 3] & (1 << (pos & 7))):
                    return False
        return True

    def _positions(self, key: str) -> list[int]:
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        h1 = int.from_bytes(digest[:8], "big")
        h2 = int.from_bytes(digest[8:16], "big") or 1
        return [(h1 + i * h2) % self.bit_size for i in range(self.hash_count)]


class LocalParentCache:
    """L1：线程安全的进程内 LRU + 随机 TTL 抖动父块缓存。"""

    def __init__(
        self,
        capacity: int = DEFAULT_L1_CAPACITY,
        ttl_seconds: float = DEFAULT_L1_TTL_SECONDS,
        jitter_seconds: float = DEFAULT_L1_JITTER_SECONDS,
        clock=time.monotonic,
        jitter_fn=random.uniform,
    ) -> None:
        self.capacity = max(0, capacity)
        self.ttl_seconds = max(0.0, ttl_seconds)
        self.jitter_seconds = max(0.0, jitter_seconds)
        self._clock = clock
        self._jitter_fn = jitter_fn
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
        """批量写入 L1 缓存，每个 key 附加独立随机 TTL 抖动以防集体过期雪崩。"""
        if self.capacity <= 0 or self.ttl_seconds <= 0 or not items:
            return
        now = self._clock()
        with self._lock:
            for pid, payload in items.items():
                jitter = (
                    float(self._jitter_fn(0.0, self.jitter_seconds))
                    if self.jitter_seconds > 0
                    else 0.0
                )
                expires_at = now + self.ttl_seconds + max(0.0, jitter)
                if pid in self._store:
                    self._store.move_to_end(pid)
                self._store[pid] = (expires_at, payload)
            while len(self._store) > self.capacity:
                self._store.popitem(last=False)
