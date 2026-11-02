# -*- coding: utf-8 -*-
"""按文档版本及更新时间构建完整 Redis Bitmap，缺失或故障时放行。"""

import hashlib
import logging
import math
import time
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence

from services.common.exceptions import StorageError
from services.retrieval.rag.fusion.backfill.cache.connection import RedisConnection
from services.retrieval.rag.fusion.backfill.models import ParentChunkRef

_MAGIC = b"RAG-BLOOM-v1:"
_HEADER_BITS = len(_MAGIC) * 8
logger = logging.getLogger("rag.retrieval.backfill")

# 一个版本的完整位图通过单次 SET 原子发布，不暴露半成品或单独的 ready 标记。
_CONTAINS_LUA = """
if redis.call('STRLEN', KEYS[1]) ~= tonumber(ARGV[1]) then
    return {-1}
end
if redis.call('GETRANGE', KEYS[1], 0, string.len(ARGV[2]) - 1) ~= ARGV[2] then
    return {-1}
end
local hashes = tonumber(ARGV[3])
local result = {}
for first = 4, #ARGV, hashes do
    local present = 1
    for offset = 0, hashes - 1 do
        if redis.call('GETBIT', KEYS[1], tonumber(ARGV[first + offset])) == 0 then
            present = 0
            break
        end
    end
    result[#result + 1] = present
end
return result
"""


class ParentBloomFilter:
    """共享过滤器；只有从权威源加载到完整版本快照后才允许否定拦截。"""

    def __init__(
        self,
        redis_url: str = "",
        client=None,
        *,
        connection: RedisConnection | None = None,
        false_positive_rate: float = 0.01,
        max_snapshot_ids: int = 100000,
        snapshot_ttl_seconds: int = 86400,
    ) -> None:
        if (
            not 0 < false_positive_rate < 1
            or min(max_snapshot_ids, snapshot_ttl_seconds) < 1
        ):
            raise ValueError("过滤器误判率须在 0 与 1 之间，快照上限须大于 0")
        self.connection = connection or RedisConnection(redis_url, client)
        self.false_positive_rate = false_positive_rate
        self.max_snapshot_ids = max_snapshot_ids
        self.snapshot_ttl_seconds = snapshot_ttl_seconds

    def _dimensions(self, count: int) -> tuple[int, int]:
        bits = max(
            64,
            math.ceil(-count * math.log(self.false_positive_rate) / math.log(2) ** 2),
        )
        bits = math.ceil(bits / 8) * 8
        hashes = max(1, round(bits / max(1, count) * math.log(2)))
        return bits, min(hashes, 32)

    def _key(self, ref: ParentChunkRef, count: int) -> str:
        bits, hashes = self._dimensions(count)
        return f"rag:parent_bloom:v1:{ref.scope}:{count}:{bits}:{hashes}"

    @staticmethod
    def _positions(parent_id: str, bits: int, hashes: int) -> list[int]:
        digest = hashlib.sha256(parent_id.encode("utf-8")).digest()
        first = int.from_bytes(digest[:8], "big")
        step = int.from_bytes(digest[8:16], "big") | 1
        return [(first + index * step) % bits for index in range(hashes)]

    def _read(self, refs, count, deadline):
        bits, hashes = self._dimensions(count)
        offsets = []
        for ref in refs:
            offsets.extend(
                pos + _HEADER_BITS
                for pos in self._positions(ref.parent_id, bits, hashes)
            )
        values = self.connection.run(
            lambda client: client.eval(
                _CONTAINS_LUA,
                1,
                self._key(refs[0], count),
                len(_MAGIC) + bits // 8,
                _MAGIC,
                hashes,
                *offsets,
            ),
            None,
            deadline=deadline,
        )
        if values is None or values == [-1] or len(values) != len(refs):
            return None
        return values

    def filter_many(
        self,
        refs: Sequence[ParentChunkRef],
        parent_counts: Mapping[str, int],
        id_loader: Callable,
        *,
        deadline: float | None = None,
    ) -> list[ParentChunkRef]:
        """完整快照首次缺失时从 MySQL 构建；过大文档及不完整快照放行。"""
        grouped = {}
        for ref in refs:
            grouped.setdefault(ref.scope, []).append(ref)
        allowed = set()
        for group in grouped.values():
            document_id = group[0].document_id
            version = group[0].version
            count = parent_counts.get(document_id, 0)
            if count < 1 or count > self.max_snapshot_ids:
                allowed.update(group)
                continue
            values = self._read(group, count, deadline)
            if values is None:
                # 熔断或未配置时不为过滤器额外查库。
                available = self.connection.run(
                    lambda client: client.ping(), False, deadline=deadline
                )
                if available:
                    try:
                        ids = id_loader(
                            document_id,
                            version,
                            count,
                            group[0].revision,
                            deadline=deadline,
                        )
                    except (StorageError, RuntimeError) as exc:
                        # 快照只是可降级的优化；构建失败不能否定正文缓存中的有效父块。
                        logger.warning(
                            "父块过滤器快照加载失败，放行查询：%s", type(exc).__name__
                        )
                        ids = []
                    if len(ids) == count and len(set(ids)) == count:
                        bits, hashes = self._dimensions(count)
                        bitmap = bytearray(bits // 8)
                        for index, pid in enumerate(ids):
                            if (
                                index % 256 == 0
                                and deadline is not None
                                and time.monotonic() >= deadline
                            ):
                                raise TimeoutError("父块过滤器构建已超时")
                            for pos in self._positions(pid, bits, hashes):
                                bitmap[pos >> 3] |= 1 << (7 - (pos & 7))
                        value = _MAGIC + bytes(bitmap)
                        self.connection.run(
                            lambda client: client.set(
                                self._key(group[0], count),
                                value,
                                ex=self.snapshot_ttl_seconds,
                            ),
                            False,
                            deadline=deadline,
                        )
                        values = self._read(group, count, deadline)
            if values is None:
                allowed.update(group)
            else:
                allowed.update(
                    ref for ref, present in zip(group, values, strict=True) if present
                )
        return [ref for ref in refs if ref in allowed]
