# -*- coding: utf-8 -*-
"""Elasticsearch 8 子块 BM25 索引。"""

import logging
import time

from config import Settings

logger = logging.getLogger("rag.indexing.elasticsearch")


class ElasticsearchChunkStore:
    def __init__(self, settings: Settings, client=None, sleep=time.sleep):
        from elasticsearch import Elasticsearch

        self.settings = settings
        self.sleep = sleep
        options = {
            "request_timeout": settings.indexing_timeout_seconds,
            "max_retries": 3,
            "retry_on_timeout": True,
            "retry_on_status": (429, 502, 503, 504),
        }
        if settings.es_api_key:
            options["api_key"] = settings.es_api_key
        elif settings.es_username:
            options["basic_auth"] = (settings.es_username, settings.es_password)
        if settings.es_ca_certs:
            options["ca_certs"] = settings.es_ca_certs
        self.client = client or Elasticsearch(settings.es_url, **options)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def ensure(self) -> None:
        from elasticsearch import BadRequestError

        index = self.settings.es_index
        if self.client.indices.exists(index=index):
            self._validate_mapping()
            return
        properties = {}
        for name in (
            "chunk_id",
            "chunk_level",
            "parent_id",
            "document_id",
            "file_type",
            "locator",
        ):
            properties[name] = {"type": "keyword"}
        for name in (
            "parent_index",
            "child_order",
            "version",
            "chunk_index",
            "page_start",
            "page_end",
            "token_count",
        ):
            properties[name] = {"type": "integer"}
        for name in ("content", "heading_path", "document_name"):
            properties[name] = {
                "type": "text",
                "analyzer": self.settings.es_analyzer,
                "search_analyzer": self.settings.es_search_analyzer,
            }
        properties["created_at"] = {"type": "date"}
        try:
            self.client.indices.create(
                index=index,
                settings={"number_of_shards": 1, "number_of_replicas": 0},
                mappings={"dynamic": "strict", "properties": properties},
            )
        except BadRequestError as exc:
            # 多个文档首次索引时，允许另一个 worker 先建好同名索引。
            if exc.error != "resource_already_exists_exception":
                raise
        self._validate_mapping()

    def _validate_mapping(self) -> None:
        mappings = self.client.indices.get_mapping(index=self.settings.es_index)
        for definition in mappings.values():
            fields = definition["mappings"].get("properties", {})
            for name in ("chunk_id", "parent_id", "document_id", "file_type"):
                if fields.get(name, {}).get("type") != "keyword":
                    raise RuntimeError(
                        "Elasticsearch mapping 不兼容，请使用新的 ES_INDEX 重建索引"
                    )
            for name in ("version", "chunk_index", "parent_index", "child_order"):
                if fields.get(name, {}).get("type") != "integer":
                    raise RuntimeError(
                        "Elasticsearch mapping 不兼容，请使用新的 ES_INDEX 重建索引"
                    )
            for name in ("content", "heading_path", "document_name"):
                field = fields.get(name, {})
                analyzer = field.get("analyzer", "standard")
                search_analyzer = field.get("search_analyzer", analyzer)
                if (
                    field.get("type") != "text"
                    or analyzer != self.settings.es_analyzer
                    or search_analyzer != self.settings.es_search_analyzer
                ):
                    raise RuntimeError(
                        "Elasticsearch 分词器不匹配，请使用新的 ES_INDEX 重建索引"
                    )

    def upsert(self, rows: list[dict]) -> None:
        pending = rows
        for attempt in range(4):
            operations = []
            for row in pending:
                operations.extend(({"index": {"_id": row["chunk_id"]}}, row))
            result = self.client.bulk(
                index=self.settings.es_index, operations=operations, refresh=False
            )
            items = result.get("items", [])
            if len(items) != len(pending):
                raise RuntimeError("Elasticsearch 批量写入结果数量不匹配")
            retry = []
            for row, item in zip(pending, items, strict=True):
                status = item.get("index", {}).get("status", 0)
                if 200 <= status < 300:
                    continue
                if status == 429 or status >= 500:
                    retry.append(row)
                else:
                    raise RuntimeError("Elasticsearch 批量写入失败，请检查 mapping 或权限")
            if not retry:
                if result.get("errors"):
                    raise RuntimeError("Elasticsearch 批量写入失败，当前版本可重放")
                return
            if attempt == 3:
                raise RuntimeError("Elasticsearch 批量写入失败，重试耗尽")
            logger.warning(
                "elasticsearch bulk retry failed_items=%d attempt=%d",
                len(retry), attempt + 1,
            )
            pending = retry
            self.sleep(2**attempt)

    def delete_version(self, document_id: str, version: int) -> None:
        self._delete(document_id, {"term": {"version": version}})

    def prune_older(self, document_id: str, version: int) -> None:
        self._delete(document_id, {"range": {"version": {"lt": version}}})

    def _delete(self, document_id: str, version_filter: dict) -> None:
        result = self.client.delete_by_query(
            index=self.settings.es_index,
            query={
                "bool": {
                    "filter": [{"term": {"document_id": document_id}}, version_filter]
                }
            },
            refresh=True,
        )
        if (
            result.get("failures")
            or result.get("timed_out")
            or result.get("version_conflicts")
        ):
            raise RuntimeError("Elasticsearch 版本清理失败")

    def flush(self) -> None:
        result = self.client.indices.refresh(index=self.settings.es_index)
        if result["_shards"]["failed"]:
            raise RuntimeError("Elasticsearch 索引刷新失败")

    def count_version(self, document_id: str, version: int) -> int:
        result = self.client.count(
            index=self.settings.es_index,
            query={"bool": {"filter": [
                {"term": {"document_id": document_id}},
                {"term": {"version": version}},
            ]}},
        )
        return int(result["count"])

    def contains(self, document_id: str, version: int, ids: list[str]) -> bool:
        result = self.client.mget(index=self.settings.es_index, ids=ids)
        found = {
            row["_id"]
            for row in result["docs"]
            if row.get("found")
            and row["_source"].get("document_id") == document_id
            and row["_source"].get("version") == version
        }
        return found == set(ids)
