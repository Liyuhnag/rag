# -*- coding: utf-8 -*-
"""文档检索编排按顺序调用召回、融合、回查和精排。"""

from datetime import datetime
from datetime import timezone

from model.document.model import Document
from model.document.model import DocumentStatus
from services.retrieval import BackfillResult
from services.retrieval import FusedParent
from services.retrieval import FusedParentCandidate
from services.retrieval import FusionResult
from services.retrieval import KeywordSearchResult
from services.retrieval import RagRetriever
from services.retrieval import RagSearchRequest
from services.retrieval import RerankOutput
from services.retrieval import VectorSearchResult


def _document() -> Document:
    now = datetime.now(timezone.utc)
    return Document(
        id="doc-1",
        name="差旅制度.pdf",
        file_type="pdf",
        size_bytes=128,
        chunk_count=2,
        status=DocumentStatus.READY,
        uploaded_at=now,
        updated_at=now,
        object_key="documents/doc-1",
        file_url="",
        previewable=False,
        preview_url=None,
        parse_version=3,
    )


def _parent() -> FusedParent:
    return FusedParent(
        parent_id="p1",
        document_id="doc-1",
        document_name="差旅制度.pdf",
        version=3,
        parent_index=1,
        file_type="pdf",
        content="住宿标准",
        heading_path="第一章",
        locator="p1",
        page_start=1,
        page_end=1,
        token_count=4,
        score=0.2,
        rrf_score=0.2,
        rerank_score=None,
        rank=1,
        matched_child_count=1,
        matched_children=(),
    )


def _candidate() -> FusedParentCandidate:
    return FusedParentCandidate(
        parent_id="p1",
        document_id="doc-1",
        version=3,
        parent_index=1,
        file_type="pdf",
        heading_path="第一章",
        locator="p1",
        page_start=1,
        page_end=1,
        rrf_score=0.2,
        rank=1,
        vector_parent_rank=1,
        keyword_parent_rank=None,
        matched_child_count=1,
        matched_children=(),
    )


class VectorFake:
    def __init__(self, result: VectorSearchResult) -> None:
        self.result = result
        self.calls = []

    def retrieve(self, request):
        self.calls.append(request)
        return self.result


class KeywordFake:
    def __init__(self, result: KeywordSearchResult) -> None:
        self.result = result
        self.calls = []

    def retrieve(self, request):
        self.calls.append(request)
        return self.result


class FuserFake:
    def __init__(self, result: FusionResult) -> None:
        self.result = result
        self.request = None

    def fuse(self, request):
        self.request = request
        return self.result


class BackfillFake:
    def __init__(self, result: BackfillResult) -> None:
        self.result = result
        self.candidates = None
        self.allowed = None

    def backfill(self, candidates, *, allowed_versions, tenant_id="default"):
        self.candidates = candidates
        self.allowed = allowed_versions
        self.tenant_id = tenant_id
        return self.result


class RerankFake:
    def __init__(self) -> None:
        self.request = None

    def rerank(self, request):
        self.request = request
        return RerankOutput(ranked=request.candidates, reranked=request.requested, model="gte-rerank-v2")


def test_dual_recall_passes_document_version_through_fusion_and_rerank():
    vector = VectorFake(VectorSearchResult(degraded=True, degraded_reason="EMBEDDING_UNAVAILABLE"))
    keyword = KeywordFake(KeywordSearchResult(degraded=True, degraded_reason="KEYWORD_UNAVAILABLE"))
    fuser = FuserFake(FusionResult(candidates=(_candidate(),)))
    backfill = BackfillFake(BackfillResult(parents=(_parent(),), missing_parent_count=1))
    reranker = RerankFake()
    retriever = RagRetriever(vector, keyword, fuser, backfill, reranker)

    result = retriever.search(
        RagSearchRequest(
            query="住宿标准",
            documents=(_document(),),
            top_k=4,
            rerank=True,
            include_children=False,
            request_id="req-1",
        )
    )

    assert vector.calls[0].document_ids == ("doc-1",)
    assert vector.calls[0].document_versions == {"doc-1": 3}
    assert keyword.calls[0].document_versions == {"doc-1": 3}
    assert fuser.request.top_k == 4
    assert fuser.request.matched_children_limit == 0
    assert backfill.allowed == {"doc-1": 3}
    assert reranker.request.requested
    assert reranker.request.top_n == 4
    assert result.parents[0].parent_id == "p1"
    assert result.reranked
    assert result.missing_parent_count == 1
    assert result.degraded_reasons == ("EMBEDDING_UNAVAILABLE", "KEYWORD_UNAVAILABLE")


def test_keyword_only_skips_vector_retrieval():
    vector = VectorFake(VectorSearchResult())
    keyword = KeywordFake(KeywordSearchResult())
    fuser = FuserFake(FusionResult(candidates=(_candidate(),)))
    backfill = BackfillFake(BackfillResult(parents=(_parent(),)))
    retriever = RagRetriever(vector, keyword, fuser, backfill)

    result = retriever.search(
        RagSearchRequest(query="第十二条", documents=(_document(),), keyword_only=True)
    )

    assert vector.calls == []
    assert len(keyword.calls) == 1
    assert fuser.request.vector_hits == ()
    assert not result.reranked
    assert result.parents[0].rerank_score is None


def test_no_documents_skips_retrieval():
    vector = VectorFake(VectorSearchResult())
    keyword = KeywordFake(KeywordSearchResult())
    fuser = FuserFake(FusionResult())
    backfill = BackfillFake(BackfillResult())
    retriever = RagRetriever(vector, keyword, fuser, backfill)

    result = retriever.search(RagSearchRequest(query="住宿标准", documents=()))

    assert result.parents == ()
    assert vector.calls == []
    assert keyword.calls == []
    assert fuser.request is None
