# -*- coding: utf-8 -*-
"""查询理解与改写（QueryRewriter）单元测试。"""

import json

import httpx
import pytest

from services.retrieval import ChatMessage
from services.retrieval import DashScopeRewriteClient
from services.retrieval import QueryRewriter
from services.retrieval import RewriteRequest


def _mock_rewrite_client(
    reply_json: dict | str,
    status_code: int = 200,
    captured: list | None = None,
) -> DashScopeRewriteClient:
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

    return DashScopeRewriteClient(
        api_key="test-key",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )


def test_self_contained_first_turn_question_skips_llm():
    llm = DashScopeRewriteClient(
        api_key="test-key",
        transport=httpx.MockTransport(
            lambda req: pytest.fail("自包含首轮问题不应调用 LLM")
        ),
    )
    rewriter = QueryRewriter(llm_client=llm)
    result = rewriter.rewrite(
        RewriteRequest(
            question="2026年华东区差旅费住宿报销标准是多少？",
            default_file_types=("pdf",),
        )
    )
    assert result.method == "skipped"
    assert result.rewritten_query == "2026年华东区差旅费住宿报销标准是多少？"
    assert result.file_types == ("pdf",)
    assert result.sub_queries == ()


def test_multi_turn_pronoun_is_resolved_by_llm():
    captured = []
    llm = _mock_rewrite_client(
        {
            "rewritten_query": "2026年华东区差旅费的住宿报销标准是多少？",
            "sub_queries": [],
            "filters": {"file_types": ["word", "pdf"]},
            "keywords": ["华东区", "住宿报销标准"],
            "is_follow_up": True,
            "confidence": 0.95,
        },
        captured=captured,
    )
    rewriter = QueryRewriter(llm_client=llm)
    history = (
        ChatMessage(role="user", content="我们公司2026年华东区差旅费制度有更新吗？"),
        ChatMessage(role="assistant", content="有的，已更新至2026版差旅制度。"),
    )
    result = rewriter.rewrite(
        RewriteRequest(
            question="那在 PDF 里它的住宿标准是多少？",
            conversation_history=history,
        )
    )
    assert result.method == "llm"
    assert result.rewritten_query == "2026年华东区差旅费的住宿报销标准是多少？"
    assert result.is_follow_up
    # "word" 自动归一为 "docx"
    assert result.file_types == ("docx", "pdf")
    assert result.keywords == ("华东区", "住宿报销标准")

    sent_payload = json.loads(captured[0]["messages"][1]["content"])
    assert len(sent_payload["history"]) == 2
    assert sent_payload["question"] == "那在 PDF 里它的住宿标准是多少？"


def test_multi_intent_question_is_decomposed_into_max_three_sub_queries():
    llm = _mock_rewrite_client(
        {
            "rewritten_query": "华东区和华南区的收入差异及原因分析",
            "sub_queries": [
                "华东区收入是多少",
                "华南区收入是多少",
                "华东区与华南区收入差异的原因是什么",
                "超出上限的第四个子问题",
            ],
            "filters": {"file_types": ["excel"]},
            "keywords": ["华东区", "华南区", "收入差异"],
            "is_follow_up": False,
            "confidence": 0.92,
        }
    )
    rewriter = QueryRewriter(llm_client=llm)
    result = rewriter.rewrite(
        RewriteRequest(
            question="对比一下华东和华南的收入差多少，以及为什么会有差异？",
            default_file_types=("pdf",),
        )
    )
    assert result.method == "llm"
    assert len(result.sub_queries) == 3
    assert result.sub_queries[0] == "华东区收入是多少"
    # 请求显式传入的 default_file_types 优先，不被 LLM 抽出的 excel 覆盖
    assert result.file_types == ("pdf",)


@pytest.mark.parametrize(
    "bad_reply",
    [
        {"rewritten_query": "猜测的问题", "confidence": 0.4},
        {"error": "server error"},
    ],
)
def test_low_confidence_or_http_error_falls_back_to_original_question(bad_reply):
    status = 500 if "error" in bad_reply else 200
    llm = _mock_rewrite_client(bad_reply, status_code=status)
    rewriter = QueryRewriter(llm_client=llm)
    result = rewriter.rewrite(RewriteRequest(question="那它呢？"))
    assert result.method == "fallback"
    assert result.rewritten_query == "那它呢？"
