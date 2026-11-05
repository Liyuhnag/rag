# -*- coding: utf-8 -*-
"""Elasticsearch 8 子块 BM25 关键词检索服务（KeywordRetriever）。

负责：
1. 构造 BM25 查询 DSL（content^2.0 / heading_path^1.2 / document_name^0.8 + match_phrase 短语提权）；
2. 下推过滤条件（chunk_level=CHILD、document_ids、file_types、page_range、document_versions）；
3. 支持 content 字段高亮（<em>...</em>）并按 (_score desc, parent_index asc, child_order asc) 稳定排序；
4. ES 故障或超时时重试 1 次，仍失败则标记 KEYWORD_UNAVAILABLE 降级返回，不阻断向量检索。
"""

import logging
import re
import time
from collections.abc import Sequence

from config import Settings
from services.retrieval.rag.keyword.models import DEFAULT_KEYWORD_TOP_K
from services.retrieval.rag.keyword.models import KeywordHit
from services.retrieval.rag.keyword.models import KeywordRetrievalInput
from services.retrieval.rag.keyword.models import KeywordSearchResult
from services.retrieval.filters import DEFAULT_MAX_QUERY_LENGTH

logger = logging.getLogger("rag.retrieval.keyword")

_WHITESPACE = re.compile(r"\s+")
_SOURCE_FIELDS = [
    "chunk_id",
    "parent_id",
    "parent_index",
    "child_order",
    "document_id",
    "version",
    "chunk_index",
    "file_type",
    "content",
    "heading_path",
    "locator",
    "page_start",
    "page_end",
]
_MAX_DOC_IDS_FILTER = 1000
_MAX_FILE_TYPES_FILTER = 20


def build_es_dsl(
    request: KeywordRetrievalInput,
    *,
    min_should_match: str = "30%",
    highlight_fragment_size: int = 120,
    highlight_fragments: int = 3,
) -> dict[str, object]:
    """根据 KeywordRetrievalInput 构造 Elasticsearch 查询 DSL。"""
    cleaned_query = _WHITESPACE.sub(" ", request.query.strip())[
        :DEFAULT_MAX_QUERY_LENGTH
    ]
    clean_docs = [doc_id.strip() for doc_id in request.document_ids if doc_id.strip()][
        :_MAX_DOC_IDS_FILTER
    ]
    size = max(1, min(request.top_k, 200)) if request.top_k else DEFAULT_KEYWORD_TOP_K

    filters: list[dict[str, object]] = [
        {"terms": {"chunk_level": ["CHILD", "child"]}},
    ]

    if request.document_versions:
        should_docs: list[dict[str, object]] = []
        for doc_id in clean_docs:
            ver = request.document_versions.get(doc_id)
            if ver is not None:
                should_docs.append(
                    {
                        "bool": {
                            "filter": [
                                {"term": {"document_id": doc_id}},
                                {"term": {"version": int(ver)}},
                            ]
                        }
                    }
                )
            else:
                should_docs.append({"term": {"document_id": doc_id}})
        filters.append({"bool": {"should": should_docs, "minimum_should_match": 1}})
    else:
        filters.append({"terms": {"document_id": clean_docs}})

    if request.file_types:
        clean_types = [
            ft.lower().strip() for ft in request.file_types if ft and ft.strip()
        ][:_MAX_FILE_TYPES_FILTER]
        if clean_types:
            filters.append({"terms": {"file_type": clean_types}})

    if (
        request.page_range is not None
        and 0 < request.page_range.start <= request.page_range.end
    ):
        filters.append({"range": {"page_end": {"gte": request.page_range.start}}})
        filters.append({"range": {"page_start": {"lte": request.page_range.end}}})

    should_clauses: list[dict[str, object]] = [
        {
            "multi_match": {
                "query": cleaned_query,
                "fields": ["content^2.0", "heading_path^1.2", "document_name^0.8"],
                "type": "best_fields",
                "operator": "or",
                "minimum_should_match": min_should_match,
            }
        },
        {
            "match_phrase": {
                "content": {
                    "query": cleaned_query,
                    "boost": 2.0,
                }
            }
        },
    ]

    # 对路由/改写阶段提取出的精确短语或条款号补充短语提权
    for token in request.extracted_keywords[:5]:
        kw = token.strip()
        if kw and kw != cleaned_query:
            should_clauses.append(
                {
                    "match_phrase": {
                        "content": {
                            "query": kw,
                            "boost": 1.5,
                        }
                    }
                }
            )

    dsl: dict[str, object] = {
        "size": size,
        "track_total_hits": False,
        "_source": _SOURCE_FIELDS,
        "sort": [
            {"_score": "desc"},
            {"parent_index": "asc"},
            {"child_order": "asc"},
        ],
        "query": {
            "bool": {
                "filter": filters,
                "should": should_clauses,
                "minimum_should_match": 1,
            }
        },
    }

    if request.include_highlight:
        dsl["highlight"] = {
            "fields": {
                "content": {
                    "fragment_size": highlight_fragment_size,
                    "number_of_fragments": highlight_fragments,
                    "pre_tags": ["<em>"],
                    "post_tags": ["</em>"],
                }
            }
        }

    return dsl


class KeywordRetriever:
    """编排 Elasticsearch BM25 子块检索与降级处理。"""

    def __init__(
        self,
        settings: Settings,
        client=None,
        *,
        timeout_seconds: float = 3.0,
        sleep=time.sleep,
    ) -> None:
        self.settings = settings
        self._client = client
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep

    def _get_client(self):
        if self._client is not None:
            return self._client
        from elasticsearch import Elasticsearch

        options: dict[str, object] = {
            "request_timeout": self.timeout_seconds,
            "max_retries": 0,
        }
        if self.settings.es_api_key:
            options["api_key"] = self.settings.es_api_key
        elif self.settings.es_username:
            options["basic_auth"] = (
                self.settings.es_username,
                self.settings.es_password,
            )
        if self.settings.es_ca_certs:
            options["ca_certs"] = self.settings.es_ca_certs
        return Elasticsearch(self.settings.es_url, **options)

    def retrieve(self, request: KeywordRetrievalInput) -> KeywordSearchResult:
        """执行 ES 关键词召回；异常时重试 1 次，仍失败返回 KEYWORD_UNAVAILABLE 降级结果。"""
        cleaned_query = _WHITESPACE.sub(" ", request.query.strip())
        if not cleaned_query or not request.document_ids:
            return KeywordSearchResult()

        clean_docs = [d.strip() for d in request.document_ids if d.strip()]
        if not clean_docs:
            return KeywordSearchResult()

        dsl = build_es_dsl(request)
        limit = int(dsl["size"])

        started = time.monotonic()
        raw_hits = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                raw_hits = self._search_es(dsl)
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Elasticsearch 关键词检索异常 attempt=%d: %s", attempt + 1, exc
                )
                if attempt == 0:
                    self.sleep(0.1)

        keyword_ms = max(0, int((time.monotonic() - started) * 1000))
        if raw_hits is None:
            return KeywordSearchResult(
                keyword_ms=keyword_ms,
                degraded=True,
                degraded_reason="KEYWORD_UNAVAILABLE",
                error_message=str(last_error) if last_error else "Elasticsearch 不可用",
            )

        hits = _parse_es_hits(raw_hits, limit)
        return KeywordSearchResult(hits=hits, keyword_ms=keyword_ms)

    def _search_es(self, dsl: dict[str, object]) -> list[dict]:
        client = self._get_client()
        owns_client = self._client is None
        try:
            response = client.search(
                index=self.settings.es_index,
                body=dsl,
                allow_partial_search_results=False,
            )
        finally:
            if owns_client and hasattr(client, "close"):
                client.close()

        hits_wrapper = response.get("hits", {}) if isinstance(response, dict) else {}
        raw_list = hits_wrapper.get("hits", [])
        return list(raw_list) if isinstance(raw_list, Sequence) else []


def _parse_es_hits(raw_hits: Sequence[dict], limit: int) -> tuple[KeywordHit, ...]:
    """解析 ES 返回的 hits 数组，组装 KeywordHit 并赋予 rank(1..N)。"""
    candidates: list[tuple[float, int, int, str, dict, str | None]] = []
    seen_chunks: set[str] = set()

    for item in raw_hits:
        source = item.get("_source") if isinstance(item.get("_source"), dict) else {}
        chunk_id = str(source.get("chunk_id") or item.get("_id") or "").strip()
        parent_id = str(source.get("parent_id") or "").strip()
        document_id = str(source.get("document_id") or "").strip()
        if not chunk_id or not parent_id or not document_id or chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)

        raw_score = item.get("_score")
        score = float(raw_score) if raw_score is not None else 0.0
        parent_index = int(source.get("parent_index", 0))
        child_order = int(source.get("child_order", 0))

        highlight_dict = item.get("highlight")
        highlight: str | None = None
        if isinstance(highlight_dict, dict):
            fragments = highlight_dict.get("content")
            if isinstance(fragments, list) and fragments:
                highlight = "……".join(str(frag) for frag in fragments if frag)

        candidates.append(
            (score, parent_index, child_order, chunk_id, source, highlight)
        )

    candidates.sort(key=lambda row: (-row[0], row[1], row[2], row[3]))

    results: list[KeywordHit] = []
    for rank, (score, parent_index, child_order, chunk_id, source, highlight) in enumerate(
        candidates[:limit], start=1
    ):
        page_start = source.get("page_start")
        page_end = source.get("page_end")
        results.append(
            KeywordHit(
                chunk_id=chunk_id,
                parent_id=parent_id,
                parent_index=parent_index,
                child_order=child_order,
                document_id=document_id,
                version=int(source.get("version", 1)),
                chunk_index=int(source.get("chunk_index", 0)),
                file_type=str(source.get("file_type", "")),
                content=str(source.get("content", "")),
                heading_path=str(source.get("heading_path", "")),
                locator=str(source.get("locator", "")),
                page_start=None if page_start is None else int(page_start),
                page_end=None if page_end is None else int(page_end),
                score=score,
                highlight=highlight,
                rank=rank,
            )
        )
    return tuple(results)
