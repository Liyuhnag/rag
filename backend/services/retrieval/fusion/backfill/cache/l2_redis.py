# -*- coding: utf-8 -*-
"""L2 二级缓存：基于 Redis MGET / Pipeline 的分布式父块缓存。"""

import json
import logging
from collections.abc import Mapping
from collections.abc import Sequence

from services.retrieval.fusion.backfill.models import ParentChunkPayload

logger = logging.getLogger("rag.retrieval.backfill")

DEFAULT_L2_TTL_SECONDS = 3600
DEFAULT_REDIS_KEY_PREFIX = "rag:parent_chunk:"


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
