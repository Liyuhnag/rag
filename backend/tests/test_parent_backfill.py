# -*- coding: utf-8 -*-
"""父块回填：临时独立 Redis 验证真实 Bitmap、Lua、批量缓存和锁。"""

import json
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from threading import Event

import pytest
import redis

from model.document_chunk import orm as chunk_orm
from services.common.exceptions import StorageError
from services.retrieval import FusedParentCandidate
from services.retrieval import MySQLParentStore
from services.retrieval import ParentBackfiller
from services.retrieval import ParentBloomFilter
from services.retrieval import ParentChunkPayload
from services.retrieval import RedisParentCache
from services.retrieval.fusion.backfill.cache.connection import RedisConnection
from services.retrieval.fusion.backfill.models import ParentChunkRef


@pytest.fixture(scope="module")
def servers():
    binary = shutil.which("redis-server")
    if binary is None:
        pytest.skip("未安装 redis-server，跳过真实 Redis 测试")
    temporary = tempfile.TemporaryDirectory(prefix="rag-redis-", dir="/tmp")
    processes = []
    clients = []
    try:
        for policy in ("allkeys-lfu", "noeviction"):
            directory = Path(temporary.name) / policy
            directory.mkdir()
            socket = str(directory / "redis.sock")
            process = subprocess.Popen(
                [
                    binary,
                    "--port",
                    "0",
                    "--unixsocket",
                    socket,
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                    "--dir",
                    str(directory),
                    "--maxmemory",
                    "64mb",
                    "--maxmemory-policy",
                    policy,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            processes.append(process)
            client = redis.Redis(unix_socket_path=socket, socket_timeout=0.2)
            clients.append(client)
            deadline = time.monotonic() + 3
            while True:
                try:
                    client.ping()
                    break
                except redis.RedisError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("临时 Redis 启动失败")
                    time.sleep(0.01)
        yield clients
    finally:
        for client in clients:
            client.close()
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        temporary.cleanup()


@pytest.fixture
def redis_pair(servers):
    for client in servers:
        client.flushdb()
    return servers


def _candidate(pid, rank=1, *, version=1):
    return FusedParentCandidate(
        parent_id=pid,
        document_id="doc-1",
        version=version,
        parent_index=rank - 1,
        file_type="pdf",
        heading_path="第一章",
        locator="page:1",
        page_start=1,
        page_end=1,
        rrf_score=0.03,
        rank=rank,
        vector_parent_rank=rank,
        keyword_parent_rank=rank,
        matched_child_count=1,
        matched_children=(),
    )


def _payload(pid, content="父块原文", *, version=1):
    return ParentChunkPayload(
        parent_id=pid,
        document_id="doc-1",
        version=version,
        document_name="员工手册.pdf",
        parent_index=0,
        file_type="pdf",
        content=content,
        heading_path="第一章",
        locator="page:1-2",
        page_start=1,
        page_end=2,
        token_count=128,
    )


def _ref(
    pid,
    *,
    version=1,
    tenant_id="default",
    doc_id="doc-1",
    revision="2026-11-02 00:00:00.000000",
):
    return ParentChunkRef(pid, doc_id, version, tenant_id, revision)


class Source:
    """只替换 MySQL 集成边界，支持发布状态变化。"""

    def __init__(self, *ids):
        self.parents = {pid: _payload(pid) for pid in ids}
        self.ready = True
        self.version = 1
        self.updated_at = datetime(2026, 11, 2)
        self.body_queries = []
        self.snapshot_queries = []
        self.document_queries = []

    def load(self, ids, *, timeout_seconds):
        assert timeout_seconds > 0
        self.body_queries.append(list(ids))
        if not self.ready:
            return []
        return [
            dict(self.parents[pid].to_dict(), id=pid)
            for pid in ids
            if pid in self.parents
        ]

    def documents(self, ids, *, timeout_seconds):
        self.document_queries.append(list(ids))
        if not self.ready or "doc-1" not in ids:
            return []
        return [
            {
                "id": "doc-1",
                "parse_version": self.version,
                "parent_chunk_count": len(self.parents),
                "name": "当前文档名称.pdf",
                "updated_at": self.updated_at,
            }
        ]

    def snapshot(self, document_id, version, count, revision, *, timeout_seconds):
        self.snapshot_queries.append((document_id, version, count))
        return [{"id": pid} for pid in self.parents]

    def store(self, **kwargs):
        return MySQLParentStore(
            db_loader=self.load,
            document_loader=self.documents,
            snapshot_loader=self.snapshot,
            **kwargs,
        )


def _service(pair, source, **kwargs):
    cache = RedisParentCache(client=pair[0], guard_client=pair[1])
    return ParentBackfiller(redis_cache=cache, mysql_store=source.store(), **kwargs)


def test_dedup_batch_fill_order_and_no_ttl(redis_pair):
    source = Source("a", "b")
    service = _service(redis_pair, source)
    service.redis.put_many({_ref("b"): source.parents["b"]})
    candidates = (_candidate("a"), _candidate("b", 2), _candidate("a", 3))
    result = service.backfill(candidates, allowed_versions={"doc-1": 1})
    assert [parent.parent_id for parent in result.parents] == ["a", "b"]
    assert result.parents[0].document_name == "当前文档名称.pdf"
    assert result.redis_hits == result.mysql_hits == 1
    assert source.body_queries == [["a"]]
    assert redis_pair[0].ttl(service.redis._key(_ref("a"))) == -1
    assert service.backfill(candidates, allowed_versions={"doc-1": 1}).redis_hits == 2
    assert len(source.body_queries) == len(source.snapshot_queries) == 1
    assert (
        redis_pair[0].config_get("maxmemory-policy")["maxmemory-policy"]
        == "allkeys-lfu"
    )
    assert (
        redis_pair[1].config_get("maxmemory-policy")["maxmemory-policy"] == "noeviction"
    )


def test_set_removes_old_ttl_and_never_stores_empty_or_oversized_payload(redis_pair):
    cache = RedisParentCache(
        client=redis_pair[0], guard_client=redis_pair[1], max_value_bytes=1024
    )
    ref = _ref("a")
    redis_pair[0].set(cache._key(ref), "old", ex=30)
    cache.put_many({ref: _payload("a")})
    assert redis_pair[0].ttl(cache._key(ref)) == -1
    cache.put_many(
        {
            _ref("empty"): _payload("empty", ""),
            _ref("large"): _payload("large", "a" * 2000),
        }
    )
    assert not redis_pair[0].exists(cache._key(_ref("empty")))
    assert not redis_pair[0].exists(cache._key(_ref("large")))


def test_keys_isolate_tenant_document_and_version(redis_pair):
    cache = RedisParentCache(client=redis_pair[0], guard_client=redis_pair[1])
    refs = [
        _ref("p"),
        _ref("p", version=2),
        _ref("p", tenant_id="other"),
        _ref("p", doc_id="other"),
    ]
    assert len({cache._key(ref) for ref in refs}) == 4
    assert len({cache._lock_key(ref) for ref in refs}) == 4


def test_shared_bloom_and_missing_snapshot_rebuild(redis_pair):
    source = Source("valid")
    refs = [_ref("valid"), _ref("nonexistent")]
    first = ParentBloomFilter(client=redis_pair[1])
    second = ParentBloomFilter(client=redis_pair[1])
    loader = source.store().get_snapshot_ids
    assert first.filter_many(refs, {"doc-1": 1}, loader) == refs[:1]
    assert second.filter_many(refs, {"doc-1": 1}, loader) == refs[:1]
    assert len(source.snapshot_queries) == 1
    redis_pair[1].delete(first._key(refs[0], 1))
    assert first.filter_many(refs, {"doc-1": 1}, loader) == refs[:1]
    assert len(source.snapshot_queries) == 2


def test_partial_snapshot_never_rejects_new_parent(redis_pair):
    source = Source("first")
    bloom = ParentBloomFilter(client=redis_pair[1])
    refs = [_ref("first"), _ref("new")]
    loader = source.store().get_snapshot_ids
    assert bloom.filter_many(refs, {"doc-1": 2}, loader) == refs
    assert redis_pair[1].dbsize() == 0
    source.parents["new"] = _payload("new")
    assert bloom.filter_many(refs, {"doc-1": 2}, loader) == refs
    assert redis_pair[1].dbsize() == 1


def test_new_version_uses_new_bloom(redis_pair):
    source = Source("old")
    service = _service(redis_pair, source)
    service.backfill((_candidate("old"),), allowed_versions={"doc-1": 1})
    source.version = 2
    source.parents = {"new": _payload("new", version=2)}
    result = service.backfill(
        (_candidate("new", version=2),), allowed_versions={"doc-1": 2}
    )
    assert result.parents[0].parent_id == "new"
    assert len(source.snapshot_queries) == 2


def test_no_negative_cache_and_later_parent_is_visible(redis_pair):
    source = Source()
    service = _service(redis_pair, source)
    assert not service.backfill(
        (_candidate("later"),), allowed_versions={"doc-1": 1}
    ).parents
    assert redis_pair[0].dbsize() == 0
    source.parents["later"] = _payload("later")
    assert service.backfill(
        (_candidate("later"),), allowed_versions={"doc-1": 1}
    ).parents


@pytest.mark.parametrize("change", ["not_ready", "version", "deleted"])
def test_cache_hit_cannot_bypass_current_document_state(redis_pair, change):
    source = Source("a")
    service = _service(redis_pair, source)
    candidates = (_candidate("a"),)
    assert service.backfill(candidates, allowed_versions={"doc-1": 1}).parents
    if change == "version":
        source.version = 2
    else:
        source.ready = False
    assert not service.backfill(candidates, allowed_versions={"doc-1": 1}).parents
    assert len(source.body_queries) == 1


def test_authorization_scope_required_and_empty_scope_never_queries(redis_pair):
    source = Source("a")
    service = _service(redis_pair, source)
    with pytest.raises(TypeError, match="allowed_versions"):
        service.backfill((_candidate("a"),))
    assert not service.backfill((_candidate("a"),), allowed_versions={}).parents
    assert source.document_queries == []


def test_state_rechecked_before_return(redis_pair):
    source = Source("a")
    service = _service(redis_pair, source)
    original = service.redis.put_many

    def write_then_unpublish(items, **kwargs):
        original(items, **kwargs)
        source.ready = False

    service.redis.put_many = write_then_unpublish
    assert not service.backfill(
        (_candidate("a"),), allowed_versions={"doc-1": 1}
    ).parents


def test_per_parent_locks_and_token_safe_release(redis_pair):
    cache = RedisParentCache(client=redis_pair[0], guard_client=redis_pair[1])
    hot = _ref("hot")
    first, waiting = cache.acquire_locks([hot, _ref("a")])
    assert not waiting
    second, waiting = cache.acquire_locks([hot, _ref("b")])
    assert waiting == [hot]
    assert list(second) == [_ref("b")]
    assert redis_pair[1].pttl(cache._lock_key(hot)) > 0
    assert redis_pair[0].dbsize() == 0
    redis_pair[1].set(cache._lock_key(hot), "new-owner", px=1000)
    cache.release_locks(first)
    assert redis_pair[1].get(cache._lock_key(hot)) == b"new-owner"
    cache.release_locks(second)


def test_first_lock_always_rechecks_cache(redis_pair):
    source = Source("hot")
    service = _service(redis_pair, source)
    original = service.redis.acquire_locks

    def acquire_after_peer_fill(refs, **kwargs):
        service.redis.put_many({ref: source.parents[ref.parent_id] for ref in refs})
        return original(refs, **kwargs)

    service.redis.acquire_locks = acquire_after_peer_fill
    assert (
        service.backfill((_candidate("hot"),), allowed_versions={"doc-1": 1}).redis_hits
        == 1
    )
    assert source.body_queries == []


def test_invalid_cached_version_during_wait_is_reloaded(redis_pair):
    source = Source("hot")
    service = _service(redis_pair, source)
    ref = _ref("hot")
    redis_pair[1].set(service.redis._lock_key(ref), "peer", px=1000)

    def peer_releases(_seconds):
        redis_pair[0].set(
            service.redis._key(ref), json.dumps(_payload("hot", version=2).to_dict())
        )
        redis_pair[1].delete(service.redis._lock_key(ref))

    service._sleep = peer_releases
    assert (
        service.backfill((_candidate("hot"),), allowed_versions={"doc-1": 1})
        .parents[0]
        .version
        == 1
    )
    assert source.body_queries == [["hot"]]


def test_concurrent_different_candidate_sets_share_hot_parent(redis_pair):
    source = Source("hot", "a", "b")
    entered = Event()
    original = source.load

    def slow_load(ids, **kwargs):
        if "hot" in ids:
            entered.set()
            time.sleep(0.08)
        return original(ids, **kwargs)

    source.load = slow_load
    service = _service(redis_pair, source)
    other_worker = _service(redis_pair, source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            service.backfill,
            (_candidate("hot"), _candidate("a", 2)),
            allowed_versions={"doc-1": 1},
        )
        assert entered.wait(timeout=1)
        second = pool.submit(
            other_worker.backfill,
            (_candidate("hot"), _candidate("b", 2)),
            allowed_versions={"doc-1": 1},
        )
        assert len(first.result(timeout=2).parents) == 2
        assert len(second.result(timeout=2).parents) == 2
    assert Counter(pid for batch in source.body_queries for pid in batch) == {
        "hot": 1,
        "a": 1,
        "b": 1,
    }


def test_lock_wait_deadline_never_bypasses_peer_lock(redis_pair):
    source = Source("hot")
    service = _service(redis_pair, source, timeout_seconds=0.05)
    key = service.redis._lock_key(_ref("hot"))
    redis_pair[1].set(key, "peer", px=1000)
    with pytest.raises(TimeoutError):
        service.backfill((_candidate("hot"),), allowed_versions={"doc-1": 1})
    assert source.body_queries == []
    assert redis_pair[1].get(key) == b"peer"


class BrokenRedis:
    def __getattr__(self, name):
        raise ConnectionError("test-only unavailable Redis")


def test_both_redis_failures_fall_back_without_losing_evidence():
    source = Source("a")
    cache = RedisParentCache(client=BrokenRedis(), guard_client=BrokenRedis())
    service = ParentBackfiller(redis_cache=cache, mysql_store=source.store())
    result = service.backfill((_candidate("a"),), allowed_versions={"doc-1": 1})
    assert result.mysql_hits == 1
    assert len(result.parents) == 1
    assert source.snapshot_queries == []


def test_circuit_breaker_and_half_open_recovery():
    now = [0.0]
    calls = []
    connection = RedisConnection(
        client=object(), failure_threshold=1, recovery_seconds=5, clock=lambda: now[0]
    )

    def fail(client):
        calls.append(client)
        raise ConnectionError("test")

    assert connection.run(fail, "fallback") == "fallback"
    assert connection.run(fail, "fallback") == "fallback"
    assert len(calls) == 1
    now[0] = 6
    assert connection.run(lambda client: "ok", "fallback") == "ok"
    assert connection.run(fail, "fallback") == "fallback"
    assert len(calls) == 2


def test_mysql_budget_shared_across_instances():
    first = Source("a").store(max_concurrency=1, acquire_timeout_seconds=0.01)
    second = Source("b").store(max_concurrency=1, acquire_timeout_seconds=0.01)
    assert first._semaphore.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="限流"):
            second.get_many(["b"])
    finally:
        first._semaphore.release()
    assert second.get_many(["b"])


def test_mysql_batches_and_deadline():
    source = Source("a", "b", "c")
    store = source.store(batch_size=2)
    assert len(store.get_many(["a", "b", "a", "c"], deadline=time.monotonic() + 1)) == 3
    assert source.body_queries == [["a", "b"], ["c"]]
    with pytest.raises(TimeoutError):
        store.get_many(["a"], deadline=time.monotonic() - 1)


def test_orm_guards_and_snapshot_id_only_query(monkeypatch):
    calls = []

    def execute(sql, params, **kwargs):
        calls.append((sql, params, kwargs))
        return []

    monkeypatch.setattr(chunk_orm, "execute", execute)
    assert chunk_orm.list_parents_by_ids([]) == []
    chunk_orm.list_parents_by_ids(["p-1"], timeout_seconds=0.5)
    sql, params, options = calls[-1]
    assert "dc.chunk_level = 'PARENT'" in sql
    assert "d.status = 'READY'" in sql
    assert "dc.version = d.parse_version" in sql
    assert params == {"id_0": "p-1"}
    assert options == {"timeout_seconds": 0.5}
    chunk_orm.list_parent_ids_for_snapshot(
        "doc-1", 1, 10, "2026-11-02 00:00:00.000000", timeout_seconds=0.5
    )
    assert "SELECT dc.id" in calls[-1][0]
    assert "d.updated_at = %(revision)s" in calls[-1][0]
    assert calls[-1][1]["limit"] == 11


def test_same_version_replacement_never_reuses_old_body_or_bloom(redis_pair):
    source = Source("old")
    service = _service(redis_pair, source)
    assert service.backfill((_candidate("old"),), allowed_versions={"doc-1": 1}).parents
    source.updated_at += timedelta(microseconds=1)
    source.parents = {"new": _payload("new", "替换后的正文")}
    result = service.backfill((_candidate("new"),), allowed_versions={"doc-1": 1})
    assert result.parents[0].content == "替换后的正文"
    source.updated_at += timedelta(microseconds=1)
    source.parents["new"] = _payload("new", "相同 ID 的新正文")
    assert (
        service.backfill((_candidate("new"),), allowed_versions={"doc-1": 1})
        .parents[0]
        .content
        == "相同 ID 的新正文"
    )
    assert len(source.snapshot_queries) == 3


def test_revision_change_during_read_discards_result(redis_pair):
    source = Source("a")
    service = _service(redis_pair, source)
    original = service.redis.put_many

    def write_then_replace(items, **kwargs):
        original(items, **kwargs)
        source.updated_at += timedelta(microseconds=1)

    service.redis.put_many = write_then_replace
    assert not service.backfill(
        (_candidate("a"),), allowed_versions={"doc-1": 1}
    ).parents


def test_corrupt_bloom_and_expired_snapshot_rebuild_without_false_negative(redis_pair):
    source = Source("a")
    bloom = ParentBloomFilter(client=redis_pair[1], snapshot_ttl_seconds=60)
    ref = _ref("a")
    key = bloom._key(ref, 1)
    redis_pair[1].set(key, b"partial bitmap")
    assert bloom.filter_many([ref], {"doc-1": 1}, source.store().get_snapshot_ids) == [
        ref
    ]
    assert 0 < redis_pair[1].ttl(key) <= 60
    redis_pair[1].pexpire(key, 1)
    time.sleep(0.01)
    assert bloom.filter_many([ref], {"doc-1": 1}, source.store().get_snapshot_ids) == [
        ref
    ]
    assert len(source.snapshot_queries) == 2


@pytest.mark.parametrize(
    "bad", [None, [], {"content": None}, {"content": "", "version": float("inf")}]
)
def test_malformed_cache_value_is_a_miss(redis_pair, bad):
    cache = RedisParentCache(client=redis_pair[0], guard_client=redis_pair[1])
    ref = _ref("a")
    data = _payload("a").to_dict()
    if isinstance(bad, dict):
        data.update(bad)
    else:
        data = bad
    redis_pair[0].set(cache._key(ref), json.dumps(data))
    assert cache.get_many([ref]) == ({}, [ref])


@pytest.mark.parametrize(
    "failure", [StorageError("snapshot unavailable"), RuntimeError("capacity reached")]
)
def test_snapshot_query_failure_preserves_cached_evidence(redis_pair, failure):
    source = Source("a")

    def unavailable(*args, **kwargs):
        raise failure

    source.snapshot = unavailable
    service = _service(redis_pair, source)
    service.redis.put_many({_ref("a"): source.parents["a"]})
    result = service.backfill((_candidate("a"),), allowed_versions={"doc-1": 1})
    assert result.redis_hits == 1
    assert len(result.parents) == 1
    assert source.body_queries == []


def test_oversized_bloom_snapshot_fails_open_without_id_scan(redis_pair):
    source = Source("a", "b")
    bloom = ParentBloomFilter(client=redis_pair[1], max_snapshot_ids=1)
    refs = [_ref("a"), _ref("missing")]
    assert (
        bloom.filter_many(refs, {"doc-1": 2}, source.store().get_snapshot_ids) == refs
    )
    assert source.snapshot_queries == []
