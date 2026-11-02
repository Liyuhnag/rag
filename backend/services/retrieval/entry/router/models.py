# -*- coding: utf-8 -*-
"""查询路由阶段的数据模型与路由决策结构。"""

from dataclasses import dataclass
from dataclasses import field
from enum import Enum

from model.document.model import Document
from model.structured_dataset.model import StructuredDataset
from services.retrieval.filters import DEFAULT_MAX_QUERY_LENGTH as DEFAULT_MAX_QUERY_LENGTH
from services.retrieval.filters import PageRange

DEFAULT_TOP_K = 10
MAX_TOP_K = 50


class RouteType(str, Enum):
    """查询路由目标类型。"""

    NL = "NL"        # 自然语言文档检索（Milvus 向量 + ES BM25 双路召回）
    SQL = "SQL"      # 结构化表格查询（基于 structured_datasets 的数据分析/Text2SQL）
    EMPTY = "EMPTY"  # 限定范围内无任何可检索文档或工作表，直接短路返回空


class SearchMode(str, Enum):
    """NL 文档检索内部的召回侧重策略。"""

    DUAL = "DUAL"                    # 默认：向量语义 + ES 关键词均衡双路召回
    KEYWORD_FIRST = "KEYWORD_FIRST"  # 精确匹配优先：含引号短语、法条/章节号、编号代码


@dataclass(frozen=True)
class RouteRequest:
    """进入查询路由器的请求参数。"""

    query: str
    top_k: int = DEFAULT_TOP_K
    document_ids: tuple[str, ...] | None = None
    file_types: tuple[str, ...] | None = None
    page_range: PageRange | None = None
    rerank: bool = False
    include_children: bool = True
    include_highlight: bool = True
    request_id: str = ""


@dataclass(frozen=True)
class RouteDecision:
    """查询路由与意图分发结果。"""

    normalized_query: str
    route_type: RouteType
    search_mode: SearchMode = SearchMode.DUAL
    ready_documents: tuple[Document, ...] = ()
    matched_datasets: tuple[StructuredDataset, ...] = ()
    extracted_keywords: tuple[str, ...] = ()
    reason: str = ""
    latency_ms: int = 0
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def ready_document_ids(self) -> tuple[str, ...]:
        """返回可参与 Milvus/ES 检索的 READY 文档 ID 元组。"""
        return tuple(doc.id for doc in self.ready_documents)

    @property
    def dataset_ids(self) -> tuple[str, ...]:
        """返回命中的结构化数据集 ID 元组。"""
        return tuple(ds.id for ds in self.matched_datasets)
