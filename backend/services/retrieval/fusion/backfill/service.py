# -*- coding: utf-8 -*-
"""父块三级回查服务（ParentBackfiller）。

查询链路：
- L1：LocalParentCache（cache/l1_local.py，进程内 LRU 内存缓存）
- L2：RedisParentCache（cache/l2_redis.py，分布式 MGET 缓存，命中后自动回填 L1）
- L3：MySQLParentStore（cache/l3_mysql.py，MySQL 批量联表查询，命中后自动回填 L2 与 L1）
"""

import logging
import time
from collections.abc import Callable
from collections.abc import Sequence

from services.retrieval.fusion.backfill.cache import LocalParentCache
from services.retrieval.fusion.backfill.cache import MySQLParentStore
from services.retrieval.fusion.backfill.cache import RedisParentCache
from services.retrieval.fusion.backfill.models import BackfillResult
from services.retrieval.fusion.backfill.models import FusedParent
from services.retrieval.fusion.backfill.models import ParentChunkPayload
from services.retrieval.fusion.models import FusedParentCandidate

logger = logging.getLogger("rag.retrieval.backfill")


class ParentBackfiller:
    """按 L1 本地内存 -> L2 Redis -> L3 MySQL 三级回查父块正文并保持 RRF 排名顺序。"""

    def __init__(
        self,
        l1_cache: LocalParentCache | None = None,
        l2_cache: RedisParentCache | None = None,
        l3_store: MySQLParentStore | None = None,
        db_loader: Callable[[Sequence[str]], list[dict[str, object]]] | None = None,
    ) -> None:
        self.l1 = l1_cache if l1_cache is not None else LocalParentCache()
        self.l2 = l2_cache if l2_cache is not None else RedisParentCache()
        self.l3 = (
            l3_store
            if l3_store is not None
            else MySQLParentStore(db_loader=db_loader)
        )

    def backfill(
        self,
        candidates: Sequence[FusedParentCandidate],
        *,
        allowed_versions: dict[str, int] | None = None,
    ) -> BackfillResult:
        """按三级缓存回查父块，严格按 candidates 的 RRF 顺序组装 FusedParent。"""
        started = time.monotonic()
        if not candidates:
            return BackfillResult()

        ordered_ids: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            if item.parent_id and item.parent_id not in seen:
                seen.add(item.parent_id)
                ordered_ids.append(item.parent_id)

        resolved: dict[str, ParentChunkPayload] = {}

        # ==================== 第一级：L1 进程内本地 LRU 缓存 ====================
        l1_found, l1_missed = self.l1.get_many(ordered_ids)
        l1_valid: dict[str, ParentChunkPayload] = {}
        l2_lookup_ids: list[str] = []
        for pid, payload in l1_found.items():
            if _is_version_valid(payload, allowed_versions):
                l1_valid[pid] = payload
            else:
                l2_lookup_ids.append(pid)
        l2_lookup_ids.extend(l1_missed)
        resolved.update(l1_valid)
        l1_hit_count = len(l1_valid)

        # ==================== 第二级：L2 Redis 分布式缓存 ====================
        l2_hit_count = 0
        l3_lookup_ids: list[str] = []
        if l2_lookup_ids:
            l2_found, l2_missed = self.l2.get_many(l2_lookup_ids)
            l2_valid: dict[str, ParentChunkPayload] = {}
            for pid, payload in l2_found.items():
                if _is_version_valid(payload, allowed_versions):
                    l2_valid[pid] = payload
                else:
                    l3_lookup_ids.append(pid)
            l3_lookup_ids.extend(l2_missed)
            if l2_valid:
                resolved.update(l2_valid)
                self.l1.put_many(l2_valid)
                l2_hit_count = len(l2_valid)

        # ==================== 第三级：L3 MySQL 批量主键回源 ====================
        l3_hit_count = 0
        if l3_lookup_ids:
            l3_found = self.l3.get_many(l3_lookup_ids)
            l3_valid = {
                pid: payload
                for pid, payload in l3_found.items()
                if _is_version_valid(payload, allowed_versions)
            }
            if l3_valid:
                resolved.update(l3_valid)
                self.l2.put_many(l3_valid)
                self.l1.put_many(l3_valid)
                l3_hit_count = len(l3_valid)

        # ==================== 按 RRF 候选顺序组装最终父块列表 ====================
        parents: list[FusedParent] = []
        missing_count = 0
        for candidate in candidates:
            payload = resolved.get(candidate.parent_id)
            if payload is None:
                missing_count += 1
                logger.warning(
                    "父块回查缺失或版本已失效: parent_id=%s document_id=%s",
                    candidate.parent_id,
                    candidate.document_id,
                )
                continue
            if payload.document_id != candidate.document_id:
                missing_count += 1
                logger.warning(
                    "父块回查 document_id 不一致: parent_id=%s (%s vs %s)",
                    candidate.parent_id,
                    payload.document_id,
                    candidate.document_id,
                )
                continue

            parents.append(
                FusedParent(
                    parent_id=payload.parent_id,
                    document_id=payload.document_id,
                    document_name=payload.document_name,
                    version=payload.version,
                    parent_index=payload.parent_index,
                    file_type=payload.file_type,
                    content=payload.content,
                    heading_path=payload.heading_path or candidate.heading_path,
                    locator=payload.locator or candidate.locator,
                    page_start=(
                        payload.page_start
                        if payload.page_start is not None
                        else candidate.page_start
                    ),
                    page_end=(
                        payload.page_end
                        if payload.page_end is not None
                        else candidate.page_end
                    ),
                    token_count=payload.token_count,
                    score=candidate.rrf_score,
                    rrf_score=candidate.rrf_score,
                    rerank_score=None,
                    rank=len(parents) + 1,
                    matched_child_count=candidate.matched_child_count,
                    matched_children=candidate.matched_children,
                )
            )

        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        return BackfillResult(
            parents=tuple(parents),
            missing_parent_count=missing_count,
            l1_hits=l1_hit_count,
            l2_hits=l2_hit_count,
            l3_hits=l3_hit_count,
            backfill_ms=elapsed_ms,
        )


def _is_version_valid(
    payload: ParentChunkPayload,
    allowed_versions: dict[str, int] | None,
) -> bool:
    if allowed_versions is None:
        return True
    expected = allowed_versions.get(payload.document_id)
    if expected is None:
        return False
    return payload.version == expected
