# -*- coding: utf-8 -*-
"""父块三级回查（L1 本地 LRU -> L2 Redis -> L3 MySQL）单元测试。"""

import json

from model.document_chunk import orm as chunk_orm
from services.retrieval import FusedParentCandidate
from services.retrieval import LocalParentCache
from services.retrieval import ParentBackfiller
from services.retrieval import ParentChunkPayload
from services.retrieval import RedisParentCache


class _FakeRedisPipeline:
    def __init__(self, store: dict[str, str]):
        self.store = store
        self.ops: list[tuple[str, int, str]] = []

    def setex(self, key: str, ttl: int, value: str):
        self.ops.append((key, ttl, value))
        return self

    def execute(self):
        for key, _ttl, value in self.ops:
            self.store[key] = value
        return [True] * len(self.ops)


class _FakeRedisClient:
    def __init__(self, initial: dict[str, str] | None = None, fail: bool = False):
        self.store = dict(initial or {})
        self.fail = fail
        self.mget_calls: list[list[str]] = []

    def mget(self, keys: list[str]):
        if self.fail:
            raise RuntimeError("redis connection refused")
        self.mget_calls.append(list(keys))
        return [self.store.get(k) for k in keys]

    def pipeline(self, transaction: bool = False):
        if self.fail:
            raise RuntimeError("redis pipeline failed")
        return _FakeRedisPipeline(self.store)


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


def test_three_tier_lookup_preserves_rrf_order_and_backfills_upper_caches():
    # RRF 排名顺序：p-mysql(第1) -> p-l1(第2) -> p-redis(第3)
    l1 = LocalParentCache(capacity=10, ttl_seconds=60.0)
    l1.put_many({"p-l1": _payload("p-l1", "来自 L1 本地缓存的父块正文")})

    fake_redis = _FakeRedisClient(
        {
            "rag:parent_chunk:p-redis": json.dumps(
                _payload("p-redis", "来自 L2 Redis 的父块正文").to_dict(),
                ensure_ascii=False,
            )
        }
    )
    l2 = RedisParentCache(client=fake_redis)

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
                "content": "来自 L3 MySQL 的父块正文",
                "heading_path": "第一章",
                "locator": "page:1",
                "page_start": 1,
                "page_end": 1,
                "token_count": 256,
            }
        ]

    backfiller = ParentBackfiller(l1_cache=l1, l2_cache=l2, db_loader=db_loader)
    candidates = (
        _candidate("p-mysql", 1, rrf_score=0.033),
        _candidate("p-l1", 2, rrf_score=0.032),
        _candidate("p-redis", 3, rrf_score=0.031),
    )

    result = backfiller.backfill(candidates)

    assert result.l1_hits == 1
    assert result.l2_hits == 1
    assert result.l3_hits == 1
    assert result.missing_parent_count == 0
    # 严格保持 RRF 候选顺序
    assert [p.parent_id for p in result.parents] == ["p-mysql", "p-l1", "p-redis"]
    assert [p.rank for p in result.parents] == [1, 2, 3]
    # L2 只查了 L1 没命中的 2 个，L3 MySQL 只查了 L1+L2 都没命中的 1 个
    assert fake_redis.mget_calls == [
        ["rag:parent_chunk:p-mysql", "rag:parent_chunk:p-redis"]
    ]
    assert db_queries == [["p-mysql"]]

    # 验证回填效果：第二次查询全部直接命中 L1，不再访问 Redis 和 MySQL
    second = backfiller.backfill(candidates)
    assert second.l1_hits == 3
    assert second.l2_hits == 0
    assert second.l3_hits == 0
    assert len(db_queries) == 1


def test_redis_failure_falls_back_to_mysql_and_missing_parents_are_counted():
    l1 = LocalParentCache(capacity=10, ttl_seconds=60.0)
    l2 = RedisParentCache(client=_FakeRedisClient(fail=True))

    def db_loader(pids):
        return [
            {
                "id": "p-1",
                "document_id": "doc-1",
                "document_name": "制度.pdf",
                "version": 1,
                "parent_index": 0,
                "file_type": "pdf",
                "content": "正文1",
                "heading_path": "一",
                "locator": "p1",
                "page_start": 1,
                "page_end": 1,
                "token_count": 50,
            }
        ]

    backfiller = ParentBackfiller(l1_cache=l1, l2_cache=l2, db_loader=db_loader)
    result = backfiller.backfill(
        (_candidate("p-1", 1), _candidate("p-missing", 2))
    )
    assert result.l3_hits == 1
    assert result.missing_parent_count == 1
    assert [p.parent_id for p in result.parents] == ["p-1"]


def test_stale_cached_version_is_evicted_when_allowed_versions_mismatch():
    l1 = LocalParentCache(capacity=10, ttl_seconds=60.0)
    l1.put_many({"p-1": _payload("p-1", "旧版本v1内容", version=1)})
    l2 = RedisParentCache(client=_FakeRedisClient())

    def db_loader(pids):
        return [
            {
                "id": "p-1",
                "document_id": "doc-1",
                "document_name": "制度.pdf",
                "version": 2,
                "parent_index": 0,
                "file_type": "pdf",
                "content": "新版本v2内容",
                "heading_path": "一",
                "locator": "p1",
                "page_start": 1,
                "page_end": 1,
                "token_count": 60,
            }
        ]

    backfiller = ParentBackfiller(l1_cache=l1, l2_cache=l2, db_loader=db_loader)
    result = backfiller.backfill(
        (_candidate("p-1", 1, version=2),),
        allowed_versions={"doc-1": 2},
    )
    assert result.l1_hits == 0
    assert result.l3_hits == 1
    assert result.parents[0].content == "新版本v2内容"


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
