# -*- coding: utf-8 -*-
"""基于 Redis MGET / Pipeline、随机 TTL 抖动与分布式互斥锁的父块缓存。"""

import hashlib
import json
import logging
import random
from collections.abc import Mapping
from collections.abc import Sequence
from uuid import uuid4

from services.retrieval.fusion.backfill.models import ParentChunkPayload

logger = logging.getLogger("rag.retrieval.backfill")

DEFAULT_REDIS_TTL_SECONDS = 3600
DEFAULT_REDIS_JITTER_SECONDS = 600
DEFAULT_LOCK_TTL_MS = 3000
DEFAULT_REDIS_KEY_PREFIX = "rag:parent_chunk:"
DEFAULT_REDIS_LOCK_PREFIX = "rag:parent_lock:"

# 仅当锁的持有者 token 匹配时才删除锁，防止误删其他进程的锁
_UNLOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class RedisParentCache:
    """基于 Redis MGET / Pipeline 的分布式父块缓存。

    内置防护：
    1. 防缓存雪崩：每个 key 写入时独立附加 [0, jitter_seconds] 随机 TTL 抖动；
    2. 防缓存击穿：提供 acquire_lock / release_lock 分布式互斥锁（SET NX PX + Lua 安全解锁）。
    """

    def __init__(
        self,
        redis_url: str = "",
        client=None,
        *,
        ttl_seconds: int = DEFAULT_REDIS_TTL_SECONDS,
        jitter_seconds: int = DEFAULT_REDIS_JITTER_SECONDS,
        lock_ttl_ms: int = DEFAULT_LOCK_TTL_MS,
        key_prefix: str = DEFAULT_REDIS_KEY_PREFIX,
        lock_prefix: str = DEFAULT_REDIS_LOCK_PREFIX,
        randint_fn=random.randint,
    ) -> None:
        self.redis_url = redis_url.strip()
        self._client = client
        self.ttl_seconds = max(1, ttl_seconds)
        self.jitter_seconds = max(0, jitter_seconds)
        self.lock_ttl_ms = max(100, lock_ttl_ms)
        self.key_prefix = key_prefix
        self.lock_prefix = lock_prefix
        self._randint = randint_fn

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

    def _lock_key(self, parent_ids: Sequence[str]) -> str:
        canonical = ",".join(sorted(set(parent_ids)))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return f"{self.lock_prefix}{digest}"

    def get_many(
        self, parent_ids: Sequence[str]
    ) -> tuple[dict[str, ParentChunkPayload], list[str]]:
        """使用单次 MGET 批量查询 Redis；Redis 不可用时全部计入 missed 交由 MySQL 兜底。"""
        if not parent_ids:
            return {}, []
        try:
            client = self._get_client()
            if client is None:
                return {}, list(parent_ids)
            keys = [self._key(pid) for pid in parent_ids]
            raw_values = client.mget(keys)
        except Exception as exc:
            logger.warning("Redis 父块缓存读取失败，降级直查 MySQL: %s", exc)
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
        """使用 Pipeline 批量写入 Redis，每个 key 附加独立随机 TTL 抖动防止集体过期雪崩。"""
        if not items:
            return
        try:
            client = self._get_client()
            if client is None:
                return
            pipe = client.pipeline(transaction=False)
            for pid, payload in items.items():
                jitter = (
                    int(self._randint(0, self.jitter_seconds))
                    if self.jitter_seconds > 0
                    else 0
                )
                ttl = self.ttl_seconds + max(0, jitter)
                serialized = json.dumps(payload.to_dict(), ensure_ascii=False)
                pipe.setex(self._key(pid), ttl, serialized)
            pipe.execute()
        except Exception as exc:
            logger.warning("Redis 父块缓存回填失败（已忽略）: %s", exc)

    def acquire_lock(self, parent_ids: Sequence[str]) -> tuple[str, str] | None:
        """防缓存击穿：尝试获取回源分布式互斥锁（SET key token NX PX）。

        返回：
        - (lock_key, token)：成功获取锁，当前进程负责查 MySQL 并回填；
        - ("", "")：Redis 未启用或异常，无需等待直接放行回源；
        - None：锁已被其他并发进程持有，当前进程应短暂等待后重查 Redis。
        """
        if not parent_ids:
            return ("", "")
        try:
            client = self._get_client()
            if client is None:
                return ("", "")
            lock_key = self._lock_key(parent_ids)
            token = uuid4().hex
            acquired = client.set(lock_key, token, nx=True, px=self.lock_ttl_ms)
            if acquired:
                return (lock_key, token)
            return None
        except Exception as exc:
            logger.warning("Redis 分布式锁获取异常，降级直接放行回源: %s", exc)
            return ("", "")

    def release_lock(self, lock_key: str, token: str) -> None:
        """使用 Lua 脚本安全释放分布式互斥锁（仅当 token 一致时删除）。"""
        if not lock_key or not token:
            return
        try:
            client = self._get_client()
            if client is None:
                return
            if hasattr(client, "eval"):
                client.eval(_UNLOCK_LUA, 1, lock_key, token)
            elif client.get(lock_key) == token:
                client.delete(lock_key)
        except Exception as exc:
            logger.warning("Redis 分布式锁释放异常（已忽略）: %s", exc)
