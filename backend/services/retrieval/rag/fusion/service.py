# -*- coding: utf-8 -*-
"""父块聚合与父块级 RRF（Reciprocal Rank Fusion）融合服务。

只负责纯内存计算：
1. 单路子块按 chunk_id 去重并重编号子块 rank(1..N)；
2. 按 parent_id 折叠每路子块命中，取最佳（最小）子块排名排列得到该路的父块排名 parent_rank(1..M)；
3. 按公式 score(parent) = Σ 1 / (K + parent_rank_i) 计算父块级 RRF 分数；
4. 按 (-rrf_score, -matched_child_count, parent_index, parent_id) 稳定排序并截取 top_k；
5. 组装每个父块下的 matched_children（双路同时命中优先，按最小子块 rank 升序，截断前 N 条）。
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field

from services.retrieval.rag.fusion.models import DEFAULT_CHILD_CONTENT_MAX_CHARS
from services.retrieval.rag.fusion.models import DEFAULT_FUSION_TOP_K
from services.retrieval.rag.fusion.models import DEFAULT_MATCHED_CHILDREN_LIMIT
from services.retrieval.rag.fusion.models import DEFAULT_RRF_K
from services.retrieval.rag.fusion.models import FusedParentCandidate
from services.retrieval.rag.fusion.models import FusionInput
from services.retrieval.rag.fusion.models import FusionResult
from services.retrieval.rag.fusion.models import MatchedChild
from services.retrieval.rag.keyword.models import KeywordHit
from services.retrieval.rag.vector.models import VectorHit

logger = logging.getLogger("rag.retrieval.fusion")


@dataclass
class _ChildAccumulator:
    chunk_id: str
    child_order: int
    content: str = ""
    highlight: str | None = None
    vector_rank: int | None = None
    keyword_rank: int | None = None
    vector_score: float | None = None
    keyword_score: float | None = None


@dataclass
class _ParentAccumulator:
    parent_id: str
    document_id: str
    version: int
    parent_index: int
    file_type: str
    heading_path: str = ""
    locator: str = ""
    page_start: int | None = None
    page_end: int | None = None
    best_vector_child_rank: int | None = None
    best_keyword_child_rank: int | None = None
    vector_parent_rank: int | None = None
    keyword_parent_rank: int | None = None
    children: dict[str, _ChildAccumulator] = field(default_factory=dict)


class RRFFuser:
    """执行父块聚合与父块级 RRF 融合。"""

    def __init__(
        self,
        rrf_k: int = DEFAULT_RRF_K,
        matched_children_limit: int = DEFAULT_MATCHED_CHILDREN_LIMIT,
        child_content_max_chars: int = DEFAULT_CHILD_CONTENT_MAX_CHARS,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k 必须为正整数")
        self.rrf_k = rrf_k
        self.matched_children_limit = max(0, matched_children_limit)
        self.child_content_max_chars = max(1, child_content_max_chars)

    def fuse(self, request: FusionInput) -> FusionResult:
        """融合向量路与关键词路子块命中，产出排序后的父块候选列表。"""
        started = time.monotonic()
        rrf_k = request.rrf_k if request.rrf_k > 0 else self.rrf_k
        top_k = max(1, min(request.top_k, 50)) if request.top_k else DEFAULT_FUSION_TOP_K
        children_limit = (
            request.matched_children_limit
            if request.matched_children_limit >= 0
            else self.matched_children_limit
        )
        max_chars = (
            request.child_content_max_chars
            if request.child_content_max_chars > 0
            else self.child_content_max_chars
        )

        parents: dict[str, _ParentAccumulator] = {}
        conflicted_parents: set[str] = set()

        # 1. 聚合向量路子块（先按 chunk_id 去重并重排子块 rank）
        for child_rank, v_hit in enumerate(
            _dedup_vector_hits(request.vector_hits), start=1
        ):
            acc = _get_or_create_parent(
                parents,
                conflicted_parents,
                parent_id=v_hit.parent_id,
                document_id=v_hit.document_id,
                version=v_hit.version,
                parent_index=v_hit.parent_index,
                file_type=v_hit.file_type,
            )
            if acc is None:
                continue
            if (
                acc.best_vector_child_rank is None
                or child_rank < acc.best_vector_child_rank
            ):
                acc.best_vector_child_rank = child_rank

            child = acc.children.get(v_hit.chunk_id)
            if child is None:
                child = _ChildAccumulator(
                    chunk_id=v_hit.chunk_id,
                    child_order=v_hit.child_order,
                )
                acc.children[v_hit.chunk_id] = child
            child.vector_rank = child_rank
            child.vector_score = v_hit.score

        # 2. 聚合关键词路子块（先按 chunk_id 去重并重排子块 rank）
        for child_rank, k_hit in enumerate(
            _dedup_keyword_hits(request.keyword_hits), start=1
        ):
            acc = _get_or_create_parent(
                parents,
                conflicted_parents,
                parent_id=k_hit.parent_id,
                document_id=k_hit.document_id,
                version=k_hit.version,
                parent_index=k_hit.parent_index,
                file_type=k_hit.file_type,
            )
            if acc is None:
                continue
            if not acc.heading_path and k_hit.heading_path:
                acc.heading_path = k_hit.heading_path
            if not acc.locator and k_hit.locator:
                acc.locator = k_hit.locator
            if acc.page_start is None and k_hit.page_start is not None:
                acc.page_start = k_hit.page_start
            if acc.page_end is None and k_hit.page_end is not None:
                acc.page_end = k_hit.page_end

            if (
                acc.best_keyword_child_rank is None
                or child_rank < acc.best_keyword_child_rank
            ):
                acc.best_keyword_child_rank = child_rank

            child = acc.children.get(k_hit.chunk_id)
            if child is None:
                child = _ChildAccumulator(
                    chunk_id=k_hit.chunk_id,
                    child_order=k_hit.child_order,
                )
                acc.children[k_hit.chunk_id] = child
            child.keyword_rank = child_rank
            child.keyword_score = k_hit.score
            if k_hit.content:
                child.content = k_hit.content[:max_chars]
            if k_hit.highlight:
                child.highlight = k_hit.highlight

        if not parents:
            return FusionResult(
                fusion_ms=max(0, int((time.monotonic() - started) * 1000))
            )

        # 3. 分别折叠出向量路与关键词路的父块排名 parent_rank (从 1 开始)
        vector_parents = [
            p for p in parents.values() if p.best_vector_child_rank is not None
        ]
        vector_parents.sort(
            key=lambda p: (
                p.best_vector_child_rank,
                p.parent_index,
                p.parent_id,
            )
        )
        for p_rank, parent in enumerate(vector_parents, start=1):
            parent.vector_parent_rank = p_rank

        keyword_parents = [
            p for p in parents.values() if p.best_keyword_child_rank is not None
        ]
        keyword_parents.sort(
            key=lambda p: (
                p.best_keyword_child_rank,
                p.parent_index,
                p.parent_id,
            )
        )
        for p_rank, parent in enumerate(keyword_parents, start=1):
            parent.keyword_parent_rank = p_rank

        # 4. 计算父块级 RRF 分数并按并列打破规则排序
        scored_parents: list[tuple[float, int, int, str, _ParentAccumulator]] = []
        for parent in parents.values():
            score = 0.0
            if parent.vector_parent_rank is not None:
                score += 1.0 / (rrf_k + parent.vector_parent_rank)
            if parent.keyword_parent_rank is not None:
                score += 1.0 / (rrf_k + parent.keyword_parent_rank)
            child_count = len(parent.children)
            scored_parents.append(
                (
                    score,
                    child_count,
                    parent.parent_index,
                    parent.parent_id,
                    parent,
                )
            )

        # 排序键：score 降序 -> matched_child_count 降序 -> parent_index 升序 -> parent_id 升序
        scored_parents.sort(
            key=lambda item: (-round(item[0], 12), -item[1], item[2], item[3])
        )

        # 5. 组装前 top_k 个父块候选及各自的 matched_children
        candidates: list[FusedParentCandidate] = []
        for final_rank, (score, child_count, _, _, parent) in enumerate(
            scored_parents[:top_k], start=1
        ):
            ordered_children = _order_matched_children(
                list(parent.children.values()), children_limit
            )
            candidates.append(
                FusedParentCandidate(
                    parent_id=parent.parent_id,
                    document_id=parent.document_id,
                    version=parent.version,
                    parent_index=parent.parent_index,
                    file_type=parent.file_type,
                    heading_path=parent.heading_path,
                    locator=parent.locator,
                    page_start=parent.page_start,
                    page_end=parent.page_end,
                    rrf_score=score,
                    rank=final_rank,
                    vector_parent_rank=parent.vector_parent_rank,
                    keyword_parent_rank=parent.keyword_parent_rank,
                    matched_child_count=child_count,
                    matched_children=ordered_children,
                )
            )

        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        return FusionResult(
            candidates=tuple(candidates),
            total_parent_candidates=len(scored_parents),
            fusion_ms=elapsed_ms,
        )


def _dedup_vector_hits(hits: Sequence[VectorHit]) -> list[VectorHit]:
    seen: set[str] = set()
    result: list[VectorHit] = []
    for hit in hits:
        if hit.chunk_id and hit.parent_id and hit.chunk_id not in seen:
            seen.add(hit.chunk_id)
            result.append(hit)
    return result


def _dedup_keyword_hits(hits: Sequence[KeywordHit]) -> list[KeywordHit]:
    seen: set[str] = set()
    result: list[KeywordHit] = []
    for hit in hits:
        if hit.chunk_id and hit.parent_id and hit.chunk_id not in seen:
            seen.add(hit.chunk_id)
            result.append(hit)
    return result


def _get_or_create_parent(
    parents: dict[str, _ParentAccumulator],
    conflicted_parents: set[str],
    *,
    parent_id: str,
    document_id: str,
    version: int,
    parent_index: int,
    file_type: str,
) -> _ParentAccumulator | None:
    if parent_id in conflicted_parents:
        return None
    existing = parents.get(parent_id)
    if existing is not None:
        if existing.document_id != document_id:
            logger.warning(
                "检测到同一 parent_id=%s 关联了不同 document_id (%s vs %s)，跳过该异常父块",
                parent_id,
                existing.document_id,
                document_id,
            )
            del parents[parent_id]
            conflicted_parents.add(parent_id)
            return None
        return existing

    created = _ParentAccumulator(
        parent_id=parent_id,
        document_id=document_id,
        version=version,
        parent_index=parent_index,
        file_type=file_type,
    )
    parents[parent_id] = created
    return created


def _order_matched_children(
    children: list[_ChildAccumulator],
    limit: int,
) -> tuple[MatchedChild, ...]:
    """排序命中子块：双路同时命中优先 -> 最小单路 rank 升序 -> child_order 升序 -> chunk_id 升序。"""
    if limit <= 0 or not children:
        return ()

    def sort_key(item: _ChildAccumulator) -> tuple[int, int, int, int, str]:
        both_hit = (
            0
            if (item.vector_rank is not None and item.keyword_rank is not None)
            else 1
        )
        v_rank = item.vector_rank if item.vector_rank is not None else 999999
        k_rank = item.keyword_rank if item.keyword_rank is not None else 999999
        min_rank = min(v_rank, k_rank)
        max_rank = max(v_rank, k_rank)
        return (both_hit, min_rank, max_rank, item.child_order, item.chunk_id)

    children.sort(key=sort_key)
    return tuple(
        MatchedChild(
            chunk_id=c.chunk_id,
            child_order=c.child_order,
            content=c.content,
            highlight=c.highlight,
            vector_rank=c.vector_rank,
            keyword_rank=c.keyword_rank,
            vector_score=c.vector_score,
            keyword_score=c.keyword_score,
        )
        for c in children[:limit]
    )
