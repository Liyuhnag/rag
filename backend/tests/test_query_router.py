# -*- coding: utf-8 -*-
"""查询路由与 LLM 意图分发单元测试。"""

import json
from datetime import datetime
from datetime import timezone

import httpx
import pytest

from model.document import orm as document_orm
from model.document.model import Document
from model.document.model import DocumentStatus
from model.structured_dataset import orm as dataset_orm
from model.structured_dataset.model import StructuredDataset
from services.retrieval import DashScopeRouterClient
from services.retrieval import PageRange
from services.retrieval import QueryRouter
from services.retrieval import RouteRequest
from services.retrieval import RouteType
from services.retrieval import SearchMode


def _doc(
    doc_id: str = "doc-1", name: str = "差旅制度.pdf", file_type: str = "pdf"
) -> Document:
    now = datetime.now(timezone.utc)
    return Document(
        id=doc_id,
        name=name,
        file_type=file_type,
        size_bytes=1024,
        chunk_count=10,
        status=DocumentStatus.READY,
        uploaded_at=now,
        updated_at=now,
        object_key=f"documents/{doc_id}/{name}",
        file_url="",
        previewable=True,
        preview_url=None,
        parse_version=1,
    )


def _dataset(
    ds_id: str = "ds-1",
    doc_id: str = "doc-csv",
    sheet_name: str = "销售明细",
    columns: tuple[str, ...] = ("华东区收入", "客户名称", "回款金额"),
) -> StructuredDataset:
    return StructuredDataset(
        id=ds_id,
        document_id=doc_id,
        version=1,
        sheet_name=sheet_name,
        file_type="xlsx",
        schema_json=[
            {"name": col, "type": "number", "nullable": False} for col in columns
        ],
        row_count=120,
        source_json_key="parsed.json",
        source_md_key="parsed.md",
    )


def _mock_llm_client(reply_json: dict | str, status_code: int = 200, captured: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(json.loads(request.content.decode("utf-8")))
        content = (
            reply_json
            if isinstance(reply_json, str)
            else json.dumps(reply_json, ensure_ascii=False)
        )
        return httpx.Response(
            status_code,
            json={"choices": [{"message": {"content": content}}]},
        )

    return DashScopeRouterClient(
        api_key="test-key",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize("invalid_query", ["", "   \n\t  ", "a" * 513])
def test_router_rejects_empty_or_overlong_query(invalid_query):
    router = QueryRouter(
        document_loader=lambda *args: [_doc()],
        dataset_loader=lambda *args: [],
    )
    with pytest.raises(ValueError):
        router.route(RouteRequest(query=invalid_query))


@pytest.mark.parametrize("bad_top_k", [0, -1, 51])
def test_router_validates_top_k_bounds(bad_top_k):
    router = QueryRouter(
        document_loader=lambda *args: [_doc()],
        dataset_loader=lambda *args: [],
    )
    with pytest.raises(ValueError, match="top_k"):
        router.route(RouteRequest(query="正常问题", top_k=bad_top_k))


def test_page_range_validates_bounds():
    assert PageRange(1, 10).start == 1
    with pytest.raises(ValueError):
        PageRange(0, 5)
    with pytest.raises(ValueError):
        PageRange(6, 5)


def test_explicit_empty_document_ids_short_circuits_without_db_or_llm():
    llm = DashScopeRouterClient(
        api_key="test-key",
        transport=httpx.MockTransport(
            lambda req: pytest.fail("空范围不应调用 LLM")
        ),
    )
    router = QueryRouter(
        llm_client=llm,
        document_loader=lambda *args: pytest.fail("空 document_ids 不应查库"),
        dataset_loader=lambda *args: pytest.fail("空 document_ids 不应查库"),
    )
    decision = router.route(RouteRequest(query="差旅标准是什么？", document_ids=()))
    assert decision.route_type == RouteType.EMPTY
    assert decision.metadata["method"] == "short_circuit"


def test_llm_router_selects_sql_and_filters_target_datasets():
    doc = _doc("doc-pdf", "财务制度.pdf", "pdf")
    ds1 = _dataset("ds-sales", "doc-xlsx", "销售明细", ("华东区收入", "回款金额"))
    ds2 = _dataset("ds-hr", "doc-hr", "考勤表", ("出勤天数", "迟到次数"))
    captured = []
    llm = _mock_llm_client(
        {
            "route_type": "SQL",
            "dataset_ids": ["ds-sales", "non-existent"],
            "search_mode": "DUAL",
            "extracted_keywords": ["华东区", "销售业绩"],
            "reason": "询问华东区销售数据汇总，匹配销售明细表",
        },
        captured=captured,
    )
    router = QueryRouter(
        llm_client=llm,
        document_loader=lambda *args: [doc],
        dataset_loader=lambda *args: [ds1, ds2],
    )
    decision = router.route(RouteRequest(query="帮我看看华东那边的业绩表现怎么样"))
    assert decision.route_type == RouteType.SQL
    assert decision.dataset_ids == ("ds-sales",)
    assert decision.extracted_keywords == ("华东区", "销售业绩")
    assert decision.reason == "询问华东区销售数据汇总，匹配销售明细表"
    assert decision.metadata["method"] == "llm"

    # 验证发给 LLM 的上下文包含了文档列表和表 Schema
    sent_user = json.loads(captured[0]["messages"][1]["content"])
    assert sent_user["query"] == "帮我看看华东那边的业绩表现怎么样"
    assert sent_user["unstructured_documents"][0]["name"] == "财务制度.pdf"
    assert sent_user["structured_datasets"][0]["sheet_name"] == "销售明细"


def test_llm_router_selects_nl_with_keyword_first_mode():
    doc = _doc("doc-pdf", "员工手册.pdf", "pdf")
    llm = _mock_llm_client(
        "```json\n"
        + json.dumps(
            {
                "route_type": "NL",
                "dataset_ids": [],
                "search_mode": "KEYWORD_FIRST",
                "extracted_keywords": ["第三章", "违约金"],
                "reason": "查询合同第三章违约金条款",
            },
            ensure_ascii=False,
        )
        + "\n```"
    )
    router = QueryRouter(
        llm_client=llm,
        document_loader=lambda *args: [doc],
        dataset_loader=lambda *args: [],
    )
    decision = router.route(RouteRequest(query="  第三章里关于违约金是怎么规定的？ "))
    assert decision.normalized_query == "第三章里关于违约金是怎么规定的？"
    assert decision.route_type == RouteType.NL
    assert decision.search_mode == SearchMode.KEYWORD_FIRST
    assert decision.extracted_keywords == ("第三章", "违约金")
    assert decision.ready_document_ids == ("doc-pdf",)
    assert decision.metadata["method"] == "llm"


def test_llm_router_guards_against_impossible_route_when_datasets_empty():
    doc = _doc("doc-pdf", "制度.pdf", "pdf")
    # 即使 LLM 误返回 SQL，但当前范围内根本没有结构化表，应自动修正为 NL
    llm = _mock_llm_client(
        {
            "route_type": "SQL",
            "dataset_ids": [],
            "search_mode": "DUAL",
            "extracted_keywords": ["总额"],
            "reason": "误判为 SQL",
        }
    )
    router = QueryRouter(
        llm_client=llm,
        document_loader=lambda *args: [doc],
        dataset_loader=lambda *args: [],
    )
    decision = router.route(RouteRequest(query="报销总额上限是多少？"))
    assert decision.route_type == RouteType.NL


def test_router_falls_back_gracefully_when_llm_fails_or_times_out():
    doc = _doc("doc-pdf", "差旅制度.pdf", "pdf")
    llm = _mock_llm_client({"error": "rate limit"}, status_code=429)
    router = QueryRouter(
        llm_client=llm,
        document_loader=lambda *args: [doc],
        dataset_loader=lambda *args: [],
    )
    decision = router.route(RouteRequest(query="按照第三章第2节执行"))
    assert decision.route_type == RouteType.NL
    assert decision.search_mode == SearchMode.KEYWORD_FIRST
    assert "第三章" in decision.extracted_keywords
    assert decision.metadata["method"] == "fallback"


def test_fallback_routes_to_sql_when_only_structured_datasets_exist():
    ds = _dataset()
    router = QueryRouter(
        llm_client=None,
        document_loader=lambda *args: [],
        dataset_loader=lambda *args: [ds],
    )
    decision = router.route(RouteRequest(query="查看所有记录"))
    assert decision.route_type == RouteType.SQL
    assert decision.dataset_ids == ("ds-1",)
    assert decision.metadata["method"] == "fallback"


def test_orm_list_ready_and_list_current_datasets_filters(monkeypatch):
    calls = []

    def fake_execute(sql, params):
        calls.append((sql, params))
        return []

    monkeypatch.setattr(document_orm, "execute", fake_execute)
    monkeypatch.setattr(dataset_orm, "execute", fake_execute)

    assert document_orm.list_ready(document_ids=[]) == []
    assert dataset_orm.list_current_datasets(document_ids=[]) == []
    assert calls == []

    document_orm.list_ready(document_ids=["d1"], file_types=["PDF"], limit=50)
    sql, params = calls[-1]
    assert "status = 'READY'" in sql
    assert "id IN (%(doc_0)s)" in sql
    assert "LOWER(file_type) IN (%(ft_0)s)" in sql
    assert params == {"limit": 50, "doc_0": "d1", "ft_0": "pdf"}

    dataset_orm.list_current_datasets(document_ids=["d2"], file_types=["XLSX"])
    sql, params = calls[-1]
    assert "sd.version = d.parse_version" in sql
    assert "sd.status = 'SCHEMA_READY'" in sql
    assert params == {"doc_0": "d2", "ft_0": "xlsx"}
