# -*- coding: utf-8 -*-
"""基于 LLM（DashScope OpenAI 兼容接口）的查询意图与路由分类器。"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from config import Settings
from model.document.model import Document
from model.structured_dataset.model import StructuredDataset
from services.retrieval.router.models import RouteType
from services.retrieval.router.models import SearchMode

logger = logging.getLogger("rag.retrieval.router")

_SYSTEM_PROMPT = """你是一个企业级 RAG 系统的查询路由器（Query Router）。
你的任务是根据用户的提问（query）以及当前知识库中可用的【非结构化文档列表】和【结构化数据表 Schema】，判断该问题应当走哪条检索链路，并输出严格的 JSON 对象。

### 路由类型（route_type）判定规则：
1. "SQL"：
   - 用户明确在询问【结构化数据表】中的数值统计、求和、均值、最大/最小值、排名、占比、同比环比、计数或满足特定条件的数据明细；
   - 且当前【结构化数据表 Schema】中存在能够回答该问题的表（sheet_name）和字段（columns）。
2. "NL"：
   - 用户询问的是规章制度、合同条款、操作流程、原因分析、背景说明、概念定义等自然语言文档内容；
   - 或者虽然问题包含数字，但【结构化数据表 Schema】中没有相关表/字段，需要从非结构化文档（PDF/Word/Markdown/TXT）中检索。

### 检索模式（search_mode）判定规则（针对 NL 检索）：
- "KEYWORD_FIRST"：当问题包含明确的法条/章节编号（如“第三章”、“3.2节”、“第十二条”）、专有编号/错误码/型号（如“ERR-404”）、或引号包裹的精确短语时选用。
- "DUAL"：其余常规自然语言提问，使用向量语义 + BM25 关键词双路混合检索。

### 输出 JSON 格式要求（不要输出任何 Markdown 代码块包裹或多余文字）：
{
  "route_type": "NL" 或 "SQL",
  "dataset_ids": ["当 route_type 为 SQL 时，按相关性排序填入命中的 dataset_id 列表；否则填空数组 []"],
  "search_mode": "DUAL" 或 "KEYWORD_FIRST",
  "extracted_keywords": ["从问题中提炼的 1~5 个核心检索实体或关键词"],
  "reason": "简短的一句话路由理由"
}"""


@dataclass(frozen=True)
class LLMRouteResult:
    """LLM 返回的结构化路由判定。"""

    route_type: RouteType
    dataset_ids: tuple[str, ...]
    search_mode: SearchMode
    extracted_keywords: tuple[str, ...]
    reason: str


class DashScopeRouterClient:
    """调用 DashScope OpenAI 兼容 chat/completions 接口执行 JSON 路由决策。"""

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

    def classify(
        self,
        query: str,
        ready_docs: Sequence[Document],
        datasets: Sequence[StructuredDataset],
    ) -> LLMRouteResult:
        """发送候选文档与表结构 Schema 给 LLM，解析返回的 JSON 路由决策。"""
        if not self.api_key:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY")

        user_payload = {
            "query": query,
            "unstructured_documents": [
                {
                    "document_id": doc.id,
                    "name": doc.name,
                    "file_type": doc.file_type,
                }
                for doc in ready_docs[:30]
            ],
            "structured_datasets": [
                {
                    "dataset_id": ds.id,
                    "document_id": ds.document_id,
                    "sheet_name": ds.sheet_name,
                    "file_type": ds.file_type,
                    "row_count": ds.row_count,
                    "columns": [
                        {"name": col.get("name", ""), "type": col.get("type", "")}
                        for col in ds.schema_json[:50]
                    ],
                }
                for ds in datasets[:20]
            ],
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
                    "temperature": 0.0,
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
            raise RuntimeError(f"LLM 路由请求失败（HTTP {response.status_code}）")

        return _parse_llm_response(response.json(), datasets)


def _parse_llm_response(
    payload: dict[str, object],
    datasets: Sequence[StructuredDataset],
) -> LLMRouteResult:
    """解析并校验 LLM 返回的 JSON 内容。"""
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
        raise RuntimeError("LLM 路由返回格式无法解析") from exc

    raw_route = str(parsed.get("route_type", "NL")).upper().strip()
    route_type = RouteType.SQL if raw_route == "SQL" else RouteType.NL

    raw_mode = str(parsed.get("search_mode", "DUAL")).upper().strip()
    search_mode = (
        SearchMode.KEYWORD_FIRST
        if raw_mode == "KEYWORD_FIRST"
        else SearchMode.DUAL
    )

    valid_ids = {ds.id for ds in datasets}
    raw_ds_ids = parsed.get("dataset_ids") or []
    dataset_ids: list[str] = []
    if isinstance(raw_ds_ids, list):
        for item in raw_ds_ids:
            ds_id = str(item).strip()
            if ds_id in valid_ids and ds_id not in dataset_ids:
                dataset_ids.append(ds_id)

    raw_keywords = parsed.get("extracted_keywords") or []
    keywords: list[str] = []
    if isinstance(raw_keywords, list):
        for item in raw_keywords[:8]:
            kw = str(item).strip()
            if kw and kw not in keywords:
                keywords.append(kw)

    reason = str(parsed.get("reason", "")).strip() or "LLM 路由判定"
    return LLMRouteResult(
        route_type=route_type,
        dataset_ids=tuple(dataset_ids),
        search_mode=search_mode,
        extracted_keywords=tuple(keywords),
        reason=reason,
    )
