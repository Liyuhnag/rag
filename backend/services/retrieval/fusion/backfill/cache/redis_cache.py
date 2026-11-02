# -*- coding: utf-8 -*-
"""无 TTL 父块正文缓存；锁存放在另一 Redis 实例。"""

import json
from collections.abc import Mapping
from collections.abc import Sequence
from uuid import uuid4

from services.retrieval.fusion.backfill.cache.connection import RedisConnection
from services.retrieval.fusion.backfill.models import ParentChunkPayload
from services.retrieval.fusion.backfill.models import ParentChunkRef

DEFAULT_LOCK_TTL_MS = 10000
DEFAULT_REDIS_KEY_PREFIX = "rag:parent:"
DEFAULT_REDIS_LOCK_PREFIX = "rag:parent_lock:"

_UNLOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class RedisParentCache:
    """正文通过普通 SET 写入，由实例 maxmemory + allkeys-lfu 管理容量。"""

    def __init__(
        self,
        redis_url: str = "",
        client=None,
        *,
        guard_url: str = "",
        guard_client=None,
        connection: RedisConnection | None = None,
        guard_connection: RedisConnection | None = None,
        lock_ttl_ms: int = DEFAULT_LOCK_TTL_MS,
        max_value_bytes: int = 1024 * 1024,
    ) -> None:
        self.connection = connection or RedisConnection(redis_url, client)
        self.guard = guard_connection or RedisConnection(guard_url, guard_client)
        self.lock_ttl_ms = max(100, lock_ttl_ms)
        self.max_value_bytes = max(1, max_value_bytes)

    def _key(self, ref: ParentChunkRef) -> str:
        return f"{DEFAULT_REDIS_KEY_PREFIX}{ref.suffix}"

    def _lock_key(self, ref: ParentChunkRef) -> str:
        return f"{DEFAULT_REDIS_LOCK_PREFIX}{ref.suffix}"

    def get_many(
        self,
        refs: Sequence[ParentChunkRef],
        *,
        deadline: float | None = None,
    ) -> tuple[dict[str, ParentChunkPayload], list[ParentChunkRef]]:
        """批量查询；损坏、身份不符或超限的数据均当作未命中。"""
        if not refs:
            return {}, []
        keys = [self._key(ref) for ref in refs]
        values = self.connection.run(
            lambda client: client.mget(keys), None, deadline=deadline
        )
        if values is None or len(values) != len(refs):
            return {}, list(refs)
        found = {}
        missed = []
        for ref, raw in zip(refs, values, strict=True):
            if not isinstance(raw, (str, bytes)):
                missed.append(ref)
                continue
            size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
            if size > self.max_value_bytes:
                missed.append(ref)
                continue
            try:
                data = json.loads(raw)
                if not isinstance(data, dict) or not isinstance(
                    data.get("content"), str
                ):
                    missed.append(ref)
                    continue
                payload = ParentChunkPayload.from_dict(data)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                missed.append(ref)
                continue
            if (
                payload.parent_id != ref.parent_id
                or payload.document_id != ref.document_id
                or payload.version != ref.version
                or not payload.content.strip()
            ):
                missed.append(ref)
                continue
            found[ref.parent_id] = payload
        return found, missed

    def put_many(
        self,
        items: Mapping[ParentChunkRef, ParentChunkPayload],
        *,
        deadline: float | None = None,
    ) -> None:
        """只缓存真实父块；普通 SET 同时清除同键历史 TTL。"""
        encoded = {}
        for ref, payload in items.items():
            if (
                payload.parent_id != ref.parent_id
                or payload.document_id != ref.document_id
                or payload.version != ref.version
                or not payload.content.strip()
            ):
                continue
            value = json.dumps(payload.to_dict(), ensure_ascii=False).encode("utf-8")
            if len(value) <= self.max_value_bytes:
                encoded[self._key(ref)] = value
        if not encoded:
            return

        def write(client):
            with client.pipeline(transaction=False) as pipe:
                for key, value in encoded.items():
                    pipe.set(key, value)
                pipe.execute()
            return True

        self.connection.run(write, False, deadline=deadline)

    def acquire_locks(
        self,
        refs: Sequence[ParentChunkRef],
        *,
        deadline: float | None = None,
    ) -> tuple[dict[ParentChunkRef, str], list[ParentChunkRef]]:
        """逐父块加锁、批量发送；故障时放行到 MySQL 并发限制。"""
        if not refs:
            return {}, []
        tokens = {ref: uuid4().hex for ref in refs}

        def acquire(client):
            with client.pipeline(transaction=False) as pipe:
                for ref, token in tokens.items():
                    pipe.set(self._lock_key(ref), token, nx=True, px=self.lock_ttl_ms)
                return pipe.execute()

        results = self.guard.run(acquire, None, deadline=deadline)
        if results is None:
            # 故障中已成功获得的锁会按租期释放；不依赖它保护数据库容量。
            return dict.fromkeys(refs, ""), []
        acquired = {}
        waiting = []
        for ref, success in zip(refs, results, strict=True):
            if success:
                acquired[ref] = tokens[ref]
            else:
                waiting.append(ref)
        return acquired, waiting

    def release_locks(self, tickets: Mapping[ParentChunkRef, str]) -> None:
        """清理不受业务截止时间阻止，只删除自己仍持有的锁。"""
        valid = {ref: token for ref, token in tickets.items() if token}
        if not valid:
            return

        def release(client):
            with client.pipeline(transaction=False) as pipe:
                for ref, token in valid.items():
                    pipe.eval(_UNLOCK_LUA, 1, self._lock_key(ref), token)
                pipe.execute()
            return True

        self.guard.run(release, False)

    def close(self) -> None:
        self.connection.close()
        self.guard.close()
