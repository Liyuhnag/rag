# -*- coding: utf-8 -*-
"""父块聚合与父块级 RRF 融合（RRFFuser）单元测试。"""

import pytest

from services.retrieval import FusionInput
from services.retrieval import KeywordHit
from services.retrieval import RRFFuser
from services.retrieval import VectorHit


def _v_hit(
    chunk_id: str,
    parent_id: str,
    rank: int,
    *,
    doc_id: str = "doc-1",
    parent_index: int = 0,
    child_order: int = 0,
    score: float = 0.8,
) -> VectorHit:
    return VectorHit(
        chunk_id=chunk_id,
        parent_id=parent_id,
        parent_index=parent_index,
        child_order=child_order,
        document_id=doc_id,
        version=1,
        chunk_index=child_order,
        file_type="pdf",
        score=score,
        rank=rank,
    )


def _k_hit(
    chunk_id: str,
    parent_id: str,
    rank: int,
    *,
    doc_id: str = "doc-1",
    parent_index: int = 0,
    child_order: int = 0,
    score: float = 10.0,
    content: str = "子块正文片段",
    highlight: str | None = "<em>高亮</em>",
) -> KeywordHit:
    return KeywordHit(
        chunk_id=chunk_id,
        parent_id=parent_id,
        parent_index=parent_index,
        child_order=child_order,
        document_id=doc_id,
        version=1,
        chunk_index=child_order,
        file_type="pdf",
        content=content,
        heading_path="第一章 > 1.1",
        locator="page:1",
        page_start=1,
        page_end=1,
        score=score,
        highlight=highlight,
        rank=rank,
    )


def test_parent_level_rrf_collapses_multiple_children_and_breaks_ties_by_child_count():
    # 向量路：p-A 命中 1 个子块(rank 1)，p-B 命中 2 个子块(rank 2, rank 3) -> 向量父块排名 A=1, B=2
    # 关键词路：p-B 命中 1 个子块(rank 1)，p-A 命中 1 个子块(rank 2) -> 关键词父块排名 B=1, A=2
    # A 和 B 的 RRF 分数完全相同：1/(60+1) + 1/(60+2)
    # 但 B 命中了 2 个不同子块（c-b1, c-b2），A 只命中了 1 个子块（c-a1），同分打破后 B 排第 1
    v_hits = (
        _v_hit("c-a1", "p-A", 1, parent_index=1, child_order=0),
        _v_hit("c-b1", "p-B", 2, parent_index=2, child_order=0),
        _v_hit("c-b2", "p-B", 3, parent_index=2, child_order=1),
    )
    k_hits = (
        _k_hit("c-b1", "p-B", 1, parent_index=2, child_order=0),
        _k_hit("c-a1", "p-A", 2, parent_index=1, child_order=0),
    )

    fuser = RRFFuser(rrf_k=60)
    result = fuser.fuse(FusionInput(vector_hits=v_hits, keyword_hits=k_hits, top_k=10))

    assert result.parent_ids == ("p-B", "p-A")
    expected_score = 1.0 / 61 + 1.0 / 62
    assert result.candidates[0].rrf_score == pytest.approx(expected_score)
    assert result.candidates[1].rrf_score == pytest.approx(expected_score)
    assert result.candidates[0].matched_child_count == 2
    assert result.candidates[1].matched_child_count == 1
    assert result.candidates[0].vector_parent_rank == 2
    assert result.candidates[0].keyword_parent_rank == 1


def test_matched_children_prioritizes_dual_hits_and_truncates_content():
    v_hits = (
        _v_hit("c-1", "p-1", 1, child_order=0),
        _v_hit("c-2", "p-1", 2, child_order=1),
        _v_hit("c-3", "p-1", 3, child_order=2),
        _v_hit("c-4", "p-1", 4, child_order=3),
    )
    # c-3 在两路都命中，应当排在 matched_children 的第一位
    k_hits = (
        _k_hit(
            "c-3",
            "p-1",
            1,
            child_order=2,
            content="长文本" * 200,
            highlight="<em>命中</em>",
        ),
    )

    fuser = RRFFuser()
    result = fuser.fuse(
        FusionInput(
            vector_hits=v_hits,
            keyword_hits=k_hits,
            matched_children_limit=3,
            child_content_max_chars=20,
        )
    )
    parent = result.candidates[0]
    assert parent.matched_child_count == 4
    assert len(parent.matched_children) == 3
    first_child = parent.matched_children[0]
    assert first_child.chunk_id == "c-3"
    assert first_child.vector_rank == 3
    assert first_child.keyword_rank == 1
    assert len(first_child.content) == 20


def test_conflicting_document_id_for_same_parent_is_skipped():
    v_hits = (_v_hit("c-1", "p-bad", 1, doc_id="doc-1"),)
    k_hits = (_k_hit("c-2", "p-bad", 1, doc_id="doc-2"),)

    result = RRFFuser().fuse(FusionInput(vector_hits=v_hits, keyword_hits=k_hits))
    assert result.candidates == ()
    assert result.total_parent_candidates == 0
