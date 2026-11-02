# -*- coding: utf-8 -*-
"""基于 LLM（DashScope OpenAI 兼容接口）的查询改写、指代消解与问题拆解客户端。"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from config import ALLOWED_FILE_TYPES
from config import Settings
from services.retrieval.rewrite.models import ChatMessage
from services.retrieval.rewrite.models import DEFAULT_MAX_SUB_QUERIES

_FILE_TYPE_ALIASES = {
    "pdf": "pdf",
    "word": "docx",
    "doc": "doc",
    "docx": "docx",
    "excel": "xlsx",
    "xlsx": "xlsx",
    "csv": "csv",
    "md": "md",
    "markdown": "md",
    "txt": "txt",
    "text": "txt",
}

_SYSTEM_PROMPT = """你是企业知识库的查询改写助手。请根据对话历史和当前问题，输出一个严格的 JSON 对象。

要求：
1. 指代消解：把“它、他、她、其、该、这个、那个、上面说的、前述”等替换为对话历史中明确出现的具体对象；
2. 口语规范化：把口语表达转成适合知识库检索的书面查询表达；
3. 问题拆解：只有当一句话包含多个独立疑问点或跨维度对比时才拆解，子查询（sub_queries）最多 3 个，且每个子查询必须去掉代词、能够独立检索；简单单意图问题不拆解，sub_queries 填空数组 []；
4. 过滤抽取：若用户明确指定了文档格式（如“在 PDF 里找”、“看 Excel 表格”），提取到 filters.file_types（可选值：pdf, doc, docx, xlsx, csv, md, txt）；未明确提及时填空数组 []；
5. 不添加历史中不存在的事实，不改变原始意图；
6. 无法确定指代对象时，rewritten_query 保留原始问题，并将 confidence 调低（如 < 0.6）；
7. 只输出 JSON，不要输出任何 Markdown 代码块包裹或多余文字。

输出 JSON 结构：
{
  "rewritten_query": "改写后的独立主查询",
  "sub_queries": ["子查询1", "子查询2"],
  "filters": {"file_types": []},
  "keywords": ["核心关键词1", "核心关键词2"],
  "is_follow_up": true,
  "confidence": 0.95
}"""


@dataclass(frozen=True)
class LLMRewriteOutput:
    """LLM 返回的查询改写结构。"""

    rewritten_query: str
    sub_queries: tuple[str, ...]
    file_types: tuple[str, ...]
    keywords: tuple[str, ...]
    is_follow_up: bool
    confidence: float


class DashScopeRewriteClient:
    """调用 DashScope OpenAI 兼容 chat/completions 执行查询改写。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str | None = None,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-plus",
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if settings is not None:
            api_key = settings.dashscope_api_key
            base_url = settings.embedding_base_url
            model = settings.router_model
        self.api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def rewrite(
        self,
        question: str,
        history: Sequence[ChatMessage] = (),
        summary: str | None = None,
    ) -> LLMRewriteOutput:
        """根据对话历史与当前问题调用 LLM 返回改写结果。"""
        if not self.api_key:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY")

        user_payload = {
            "conversation_summary": summary or "",
            "history": [
                {"role": msg.role, "content": msg.content}
                for msg in history[-6:]
            ],
            "question": question,
        }

        with httpx.Client(
            transport=self.transport,
            timeout=httpx.Timeout(self.timeout_seconds, connect=3.0),
        ) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0.1,
                    "max_tokens": 512,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(user_payload, ensure_ascii=False),
                        },
                    ],
                },
            )

        if not response.is_success:
            raise RuntimeError(f"LLM 查询改写请求失败（HTTP {response.status_code}）")

        return _parse_rewrite_response(response.json(), question)


def _parse_rewrite_response(
    payload: dict[str, object],
    original_question: str,
) -> LLMRewriteOutput:
    """解析并校验 LLM 改写返回的 JSON。"""
    try:
        choices = payload["choices"]
        content = choices[0]["message"]["content"]
        if isinstance(content, str):
            text = content.strip()
            if text.startswith("```"):
                text = text.strip("`").removeprefix("json").strip()
            parsed = json.loads(text)
        elif isinstance(content, dict):
            parsed = content
        else:
            raise ValueError("content 类型无效")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("LLM 查询改写返回格式无法解析") from exc

    rewritten = str(parsed.get("rewritten_query", "")).strip() or original_question

    raw_subs = parsed.get("sub_queries") or []
    sub_queries: list[str] = []
    if isinstance(raw_subs, list):
        for item in raw_subs:
            sub = str(item).strip()
            if sub and sub != rewritten and sub not in sub_queries:
                sub_queries.append(sub)
            if len(sub_queries) >= DEFAULT_MAX_SUB_QUERIES:
                break

    filters = parsed.get("filters") or {}
    raw_types = filters.get("file_types") if isinstance(filters, dict) else []
    file_types: list[str] = []
    if isinstance(raw_types, list):
        for item in raw_types:
            mapped = _FILE_TYPE_ALIASES.get(str(item).lower().strip(), "")
            if mapped in ALLOWED_FILE_TYPES and mapped not in file_types:
                file_types.append(mapped)

    raw_keywords = parsed.get("keywords") or []
    keywords: list[str] = []
    if isinstance(raw_keywords, list):
        for item in raw_keywords[:10]:
            kw = str(item).strip()
            if kw and kw not in keywords:
                keywords.append(kw)

    is_follow_up = bool(parsed.get("is_follow_up", False))
    try:
        confidence = float(parsed.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return LLMRewriteOutput(
        rewritten_query=rewritten,
        sub_queries=tuple(sub_queries),
        file_types=tuple(file_types),
        keywords=tuple(keywords),
        is_follow_up=is_follow_up,
        confidence=confidence,
    )
