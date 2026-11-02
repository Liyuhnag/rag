# -*- coding: utf-8 -*-
"""父块回查（布隆过滤器 -> Redis -> MySQL）与缓存三大问题（穿透/击穿/雪崩）单元测试。"""

import json
import pytest

from model.document_chunk import orm as chunk_orm
from services.retrieval import FusedParentCandidate
from services.retrieval import MySQLParentStore
from services.retrieval import ParentBackfiller
from services.retrieval import ParentBloomFilter
from services.retrieval import ParentChunkPayload
from services.retrieval import RedisParentCache


class _FakeRedisPipeline:
    def __init__(self, store: dict[str, str], ttls: dict[str, int]):
        self.store = store
        self.ttls = ttls
        self.ops: list[tuple[str, int, str]] = []

    def setex(self, key: str, ttl: int, value: str):
        self.ops.append((key, ttl, value))
        return self

    def execute(self):
        for key, ttl, value in self.ops:
            self.store[key] = value
            self.ttls[key] = ttl
        return [True] * len(self.ops)


class _FakeRedisClient:
    def __init__(self, initial: dict[str, str] | None = None, fail: bool = False):
        self.store = dict(initial or {})
        self.ttls: dict[str, int] = {}
        self.fail = fail
        self.mget_calls: list[list[str]] = []

    def mget(self, keys: list[str]):
        if self.fail:
            raise RuntimeError("redis connection refused")
        self.mget_calls.append(list(keys))
        return [self.store.get(k) for k in keys]

    def set(self, key: str, value: str, nx: bool = False, px: int | None = None):
        if self.fail:
            raise RuntimeError("redis set failed")
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key: str):
        return self.store.get(key)

    def delete(self, key: str):
        self.store.pop(key, None)
        return 1

    def eval(self, script: str, numkeys: int, key: str, token: str):
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0

    def pipeline(self, transaction: bool = False):
        if self.fail:
            raise RuntimeError("redis pipeline failed")
        return _FakeRedisPipeline(self.store, self.ttls)


def _candidate(
    parent_id: str,
    rank: int,
    *,
    doc_id: str = "doc-1",
    version: int = 1,
    rrf_score: float = 0.03,
) -> FusedParentCandidate:
    return FusedParentCandidate(
        parent_id=parent_id,
        document_id=doc_id,
        version=version,
        parent_index=rank - 1,
        file_type="pdf",
        heading_path="第一章",
        locator="page:1",
        page_start=1,
        page_end=1,
        rrf_score=rrf_score,
        rank=rank,
        vector_parent_rank=rank,
        keyword_parent_rank=rank,
        matched_child_count=1,
        matched_children=(),
    )


def _payload(
    parent_id: str,
    content: str,
    *,
    doc_id: str = "doc-1",
    version: int = 1,
) -> ParentChunkPayload:
    return ParentChunkPayload(
        parent_id=parent_id,
        document_id=doc_id,
        document_name="员工手册.pdf",
        version=version,
        parent_index=0,
        file_type="pdf",
        content=content,
        heading_path="第一章 总则",
        locator="page:1-2",
        page_start=1,
        page_end=2,
        token_count=128,
    )


def test_lookup_preserves_rrf_order_and_backfills_redis_from_mysql():
    fake_redis = _FakeRedisClient(
        {
            "rag:parent_chunk:p-redis": json.dumps(
                _payload("p-redis", "来自 Redis 的父块正文").to_dict(),
                ensure_ascii=False,
            )
        }
    )
    redis_cache = RedisParentCache(client=fake_redis)

    db_queries: list[list[str]] = []

    def db_loader(pids):
        db_queries.append(list(pids))
        return [
            {
                "id": "p-mysql",
                "document_id": "doc-1",
                "document_name": "员工手册.pdf",
                "version": 1,
                "parent_index": 0,
                "file_type": "pdf",
                "content": "来自 MySQL 的父块正文",
                "heading_path": "第一章",
                "locator": "page:1",
                "page_start": 1,
                "page_end": 1,
                "token_count": 256,
            }
        ]

    backfiller = ParentBackfiller(redis_cache=redis_cache, db_loader=db_loader)
    candidates = (
        _candidate("p-mysql", 1, rrf_score=0.033),
        _candidate("p-redis", 2, rrf_score=0.031),
    )

    result = backfiller.backfill(candidates)

    assert result.redis_hits == 1
    assert result.mysql_hits == 1
    assert result.missing_parent_count == 0
    assert [p.parent_id for p in result.parents] == ["p-mysql", "p-redis"]
    assert [p.rank for p in result.parents] == [1, 2]
    assert db_queries == [["p-mysql"]]

    # 第二次查询全部命中 Redis，不再查 MySQL
    second = backfiller.backfill(candidates)
    assert second.redis_hits == 2
    assert second.mysql_hits == 0
    assert len(db_queries) == 1


def test_bloom_filter_prevents_cache_penetration_without_touching_redis_or_mysql():
    bloom = ParentBloomFilter(bit_size=4096, hash_count=4)
    bloom.add_many(["p-valid-1", "p-valid-2"])

    fake_redis = _FakeRedisClient()
    db_calls: list[list[str]] = []
    backfiller = ParentBackfiller(
        redis_cache=RedisParentCache(client=fake_redis),
        bloom_filter=bloom,
        db_loader=lambda pids: db_calls.append(list(pids)) or [],
    )

    result = backfiller.backfill((_candidate("p-forged-nonexistent-id", 1),))
    assert result.missing_parent_count == 1
    assert result.parents == ()
    assert fake_redis.mget_calls == []
    assert db_calls == []


def test_distributed_lock_prevents_cache_breakdown_by_waiting_for_peer_fill():
    fake_redis = _FakeRedisClient()
    redis_cache = RedisParentCache(client=fake_redis)

    lock_key = redis_cache._lock_key(["p-hot"])
    fake_redis.store[lock_key] = "other-worker-token"

    db_calls: list[list[str]] = []

    def simulate_peer_backfill(_seconds: float):
        fake_redis.store["rag:parent_chunk:p-hot"] = json.dumps(
            _payload("p-hot", "同伴进程已回填的热点父块").to_dict(),
            ensure_ascii=False,
        )
        fake_redis.store.pop(lock_key, None)

    backfiller = ParentBackfiller(
        redis_cache=redis_cache,
        db_loader=lambda pids: db_calls.append(list(pids)) or [],
        sleep=simulate_peer_backfill,
    )

    result = backfiller.backfill((_candidate("p-hot", 1),))
    assert db_calls == []
    assert result.redis_hits == 1
    assert result.mysql_hits == 0
    assert result.parents[0].content == "同伴进程已回填的热点父块"


def test_ttl_jitter_and_mysql_concurrency_limit_prevent_cache_avalanche():
    fake_redis = _FakeRedisClient()
    seq = iter([15, 240, 580])
    redis_cache = RedisParentCache(
        client=fake_redis,
        ttl_seconds=3600,
        jitter_seconds=600,
        randint_fn=lambda _a, _b: next(seq),
    )
    redis_cache.put_many(
        {
            "p-1": _payload("p-1", "a"),
            "p-2": _payload("p-2", "b"),
            "p-3": _payload("p-3", "c"),
        }
    )
    assert fake_redis.ttls["rag:parent_chunk:p-1"] == 3615
    assert fake_redis.ttls["rag:parent_chunk:p-2"] == 3840
    assert fake_redis.ttls["rag:parent_chunk:p-3"] == 4180

    store = MySQLParentStore(
        db_loader=lambda pids: [],
        max_concurrency=1,
        acquire_timeout_seconds=0.05,
    )
    assert store._semaphore.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="限流保护"):
            store.get_many(["p-1"])
    finally:
        store._semaphore.release()


def test_orm_list_parents_by_ids_sql(monkeypatch):
    calls = []
    monkeypatch.setattr(
        chunk_orm, "execute", lambda sql, params: calls.append((sql, params)) or []
    )
    assert chunk_orm.list_parents_by_ids([]) == []
    chunk_orm.list_parents_by_ids(["p-1", "p-2"])
    sql, params = calls[0]
    assert "dc.chunk_level = 'PARENT'" in sql
    assert "d.status = 'READY'" in sql
    assert "dc.version = d.parse_version" in sql
    assert params == {"id_0": "p-1", "id_1": "p-2"}
