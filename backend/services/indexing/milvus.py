# -*- coding: utf-8 -*-
"""Milvus 子块向量集合与按版本清理。"""

import json
import logging
import time

from config import Settings

logger = logging.getLogger("rag.indexing.milvus")


class MilvusChunkStore:
    def __init__(self, settings: Settings, client=None, sleep=time.sleep):
        from pymilvus import MilvusClient

        self.settings = settings
        self.sleep = sleep
        self.client = client or MilvusClient(
            uri=settings.milvus_uri,
            token=settings.milvus_token,
            db_name=settings.milvus_database,
            timeout=settings.indexing_timeout_seconds,
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def ensure(self) -> None:
        from pymilvus import DataType
        from pymilvus import MilvusClient
        from pymilvus.exceptions import MilvusException

        name = self.settings.milvus_collection
        description = f"rag-child-embeddings:{self.settings.embedding_model}"
        timeout = self.settings.indexing_timeout_seconds
        if not self.client.has_collection(name, timeout=timeout):
            schema = MilvusClient.create_schema(
                auto_id=False,
                enable_dynamic_field=False,
                description=description,
            )
            schema.add_field(
                "chunk_id", DataType.VARCHAR, is_primary=True, max_length=64
            )
            for field, length in (
                ("parent_id", 64),
                ("document_id", 32),
                ("file_type", 32),
            ):
                schema.add_field(field, DataType.VARCHAR, max_length=length)
            for field in ("parent_index", "child_order", "version", "chunk_index"):
                schema.add_field(field, DataType.INT64)
            schema.add_field(
                "vector", DataType.FLOAT_VECTOR, dim=self.settings.embedding_dim
            )
            indexes = MilvusClient.prepare_index_params()
            indexes.add_index(
                "vector",
                index_type="HNSW",
                metric_type="COSINE",
                params={"M": 16, "efConstruction": 200},
            )
            try:
                self.client.create_collection(
                    name,
                    schema=schema,
                    index_params=indexes,
                    consistency_level="Strong",
                    timeout=timeout,
                )
            except MilvusException:
                # 并发首次建集合时仍在下面校验实际 schema。
                if not self.client.has_collection(name, timeout=timeout):
                    raise
        collection = self.client.describe_collection(name, timeout=timeout)
        fields = {field["name"]: field for field in collection["fields"]}
        vector = fields.get("vector", {})
        if int(vector.get("params", {}).get("dim", 0)) != self.settings.embedding_dim:
            raise RuntimeError(
                "Milvus 向量维度不匹配，请配置新的 MILVUS_COLLECTION 后重建索引"
            )
        if collection.get("description") != description:
            raise RuntimeError(
                "Milvus 集合的 embedding 模型不匹配，请使用新的 MILVUS_COLLECTION"
            )
        required = {
            "chunk_id",
            "parent_id",
            "parent_index",
            "child_order",
            "document_id",
            "version",
            "chunk_index",
            "file_type",
            "vector",
        }
        if not required.issubset(fields) or not fields["chunk_id"].get("is_primary"):
            raise RuntimeError(
                "Milvus collection schema 不兼容，请使用新的 MILVUS_COLLECTION"
            )
        self.client.load_collection(name, timeout=timeout)

    def upsert(self, rows: list[dict]) -> None:
        from pymilvus.exceptions import MilvusException

        for attempt in range(4):
            try:
                result = self.client.upsert(
                    self.settings.milvus_collection,
                    data=rows,
                    timeout=self.settings.indexing_timeout_seconds,
                )
            except MilvusException:
                if attempt == 3:
                    raise RuntimeError("Milvus 批量写入失败，重试耗尽") from None
                logger.warning(
                    "milvus upsert retry count=%d attempt=%d", len(rows), attempt + 1
                )
                self.sleep(2**attempt)
                continue
            if result.get("upsert_count") != len(rows):
                raise RuntimeError("Milvus 未确认全部子块写入")
            logger.info("milvus upsert count=%d", len(rows))
            return

    def delete_version(self, document_id: str, version: int) -> None:
        self._delete(document_id, f"version == {int(version)}")

    def prune_older(self, document_id: str, version: int) -> None:
        self._delete(document_id, f"version < {int(version)}")

    def _delete(self, document_id: str, version_filter: str) -> None:
        literal = json.dumps(document_id, ensure_ascii=True)
        self.client.delete(
            self.settings.milvus_collection,
            filter=f"document_id == {literal} and {version_filter}",
            timeout=self.settings.indexing_timeout_seconds,
        )

    def flush(self) -> None:
        self.client.flush(
            self.settings.milvus_collection,
            timeout=self.settings.indexing_timeout_seconds,
        )

    def count_version(self, document_id: str, version: int) -> int:
        literal = json.dumps(document_id, ensure_ascii=True)
        result = self.client.query(
            self.settings.milvus_collection,
            filter=f"document_id == {literal} and version == {int(version)}",
            output_fields=["count(*)"],
            timeout=self.settings.indexing_timeout_seconds,
        )
        return int(result[0]["count(*)"])

    def contains(self, document_id: str, version: int, ids: list[str]) -> bool:
        result = self.client.get(
            self.settings.milvus_collection,
            ids=ids,
            output_fields=["chunk_id", "document_id", "version"],
            timeout=self.settings.indexing_timeout_seconds,
        )
        found = {
            row["chunk_id"]
            for row in result
            if row["document_id"] == document_id and row["version"] == version
        }
        return found == set(ids)
