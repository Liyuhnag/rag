# -*- coding: utf-8 -*-
"""检索入口按改写结果把请求分发给文档检索或结构化查询。"""

from datetime import datetime
from datetime import timezone

from model.document.model import Document
from model.document.model import DocumentStatus
from model.structured_dataset.model import StructuredDataset
from services.retrieval import RagSearchResult
from services.retrieval import RetrievalEntry
from services.retrieval import RetrievalRequest
from services.retrieval import RewriteResult
from services.retrieval import RouteDecision
from services.retrieval import RouteType
from services.retrieval import SearchMode
from services.retrieval import StructuredQueryResult
from services.retrieval.structured.service import StructuredQueryService


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


def _dataset() -> StructuredDataset:
    return StructuredDataset(
        id="ds-1",
        document_id="doc-xlsx",
        version=1,
        sheet_name="销售明细",
        file_type="xlsx",
        schema_json=[{"name": "金额", "type": "number", "nullable": False}],
        row_count=10,
        source_json_key="parsed.json",
        source_md_key="parsed.md",
    )


class RewriteFake:
    def __init__(self) -> None:
        self.request = None

    def rewrite(self, request):
        self.request = request
        return RewriteResult(
            original_question=request.question,
            rewritten_query="2026年华东区差旅住宿标准",
            file_types=("pdf",),
            method="llm",
        )


class RouterFake:
    def __init__(self, route_type: RouteType, search_mode: SearchMode = SearchMode.DUAL) -> None:
        self.route_type = route_type
        self.search_mode = search_mode
        self.request = None

    def route(self, request):
        self.request = request
        return RouteDecision(
            normalized_query=request.query,
            route_type=self.route_type,
            search_mode=self.search_mode,
            ready_documents=(_document(),) if self.route_type == RouteType.NL else (),
            matched_datasets=(_dataset(),) if self.route_type == RouteType.SQL else (),
            reason="测试路由",
        )


class RagFake:
    def __init__(self) -> None:
        self.request = None

    def search(self, request):
        self.request = request
        return RagSearchResult()


class StructuredFake:
    def __init__(self) -> None:
        self.request = None

    def query(self, request):
        self.request = request
        return StructuredQueryResult(dataset_ids=("ds-1",), message="结构化数据尚未入库，不能执行查询")


def _entry(route_type: RouteType, search_mode: SearchMode = SearchMode.DUAL):
    rewriter = RewriteFake()
    router = RouterFake(route_type, search_mode)
    rag = RagFake()
    structured = StructuredFake()
    service = RetrievalEntry(rewriter, router, rag, structured)
    return service, rewriter, router, rag, structured


def test_document_route_uses_rewritten_query_and_skips_tables():
    service, rewriter, router, rag, structured = _entry(RouteType.NL)
    result = service.retrieve(RetrievalRequest(question="那住宿标准呢", top_k=5, rerank=True))

    assert rewriter.request.question == "那住宿标准呢"
    assert router.request.query == "2026年华东区差旅住宿标准"
    assert router.request.file_types == ("pdf",)
    assert result.route_type == RouteType.NL
    assert result.rewritten_query == "2026年华东区差旅住宿标准"
    assert rag.request.query == "2026年华东区差旅住宿标准"
    assert rag.request.documents[0].id == "doc-1"
    assert rag.request.top_k == 5
    assert rag.request.rerank
    assert not rag.request.keyword_only
    assert structured.request is None
    assert result.dataset_ids == ()


def test_keyword_first_route_disables_vector_recall():
    service, _rewriter, _router, rag, _structured = _entry(
        RouteType.NL,
        SearchMode.KEYWORD_FIRST,
    )
    service.retrieve(RetrievalRequest(question="第十二条报销标准"))
    assert rag.request.keyword_only


def test_empty_route_calls_neither_retriever():
    service, _rewriter, _router, rag, structured = _entry(RouteType.EMPTY)
    result = service.retrieve(RetrievalRequest(question="没有任何资料"))
    assert result.route_type == RouteType.EMPTY
    assert result.reason == "测试路由"
    assert result.parents == ()
    assert rag.request is None
    assert structured.request is None


def test_table_route_calls_structured_query_and_skips_documents():
    service, _rewriter, _router, rag, _structured = _entry(RouteType.SQL)
    service._structured = StructuredQueryService()
    result = service.retrieve(RetrievalRequest(question="华东区收入合计是多少"))
    assert result.route_type == RouteType.SQL
    assert result.dataset_ids == ("ds-1",)
    assert not result.structured_ready
    assert result.message == "结构化数据尚未入库，不能执行查询"
    assert rag.request is None
