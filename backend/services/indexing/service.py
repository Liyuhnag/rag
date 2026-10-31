# -*- coding: utf-8 -*-
"""在版本锁内重放子块，双索引确认成功后发布 READY。"""

from collections import deque
from contextlib import ExitStack
from datetime import datetime
from datetime import timezone

from config import load_settings
from model.document import orm as document_orm
from model.document.model import Document
from model.document.model import DocumentStatus
from model.document_chunk import orm as chunk_orm
from services.indexing.embedding import DashScopeEmbedder
from services.indexing.elasticsearch import ElasticsearchChunkStore
from services.indexing.milvus import MilvusChunkStore


class DocumentIndexingService:
    """仅消费已持久化子块；同版本重试先清理，再按确定性 ID 写入。"""

    def __init__(self, settings=None, embedder=None, vectors=None, keywords=None):
        self.settings = settings or load_settings()
        self.embedder = embedder
        self.vectors = vectors
        self.keywords = keywords

    def run(self, document: Document, version: int, heartbeat=None, lease=None) -> bool:
        """过期版本不触碰外部索引；异常保留原始块以供重放。"""
        while True:
            # 先提交 PROCESSING，读者才能在清空当前索引之前看到文档不可检索。
            if not document_orm.mark_processing(
                document.id, version, datetime.now(timezone.utc)
            ):
                return False
            published = self._index_locked(document, version, heartbeat, lease)
            if published is not None:
                return published

    def _index_locked(
        self, document: Document, version: int, heartbeat, lease
    ) -> bool | None:
        """锁内仍是 READY 时不删外部索引，交回 run 再次取消发布。"""
        with chunk_orm.index_current_version(document.id, version) as session:
            if session is None:
                return False
            if session.status != DocumentStatus.PROCESSING.value:
                return None
            session.validate_children()
            embedder = self.embedder or DashScopeEmbedder(self.settings)
            # 客户端属于本次调用，在成功或失败后均关闭网络连接。
            with ExitStack() as stack:
                vectors = self.vectors or stack.enter_context(
                    MilvusChunkStore(self.settings)
                )
                keywords = self.keywords or stack.enter_context(
                    ElasticsearchChunkStore(self.settings)
                )
                vectors.ensure()
                keywords.ensure()
                if lease is not None:
                    lease()
                vectors.delete_version(document.id, version)
                keywords.delete_version(document.id, version)
                count = 0
                keyword_pending = []
                first_ids = []
                last_ids = deque(maxlen=5)
                for chunks in session.iter_children(self.settings.indexing_batch_size):
                    texts = [
                        "\n".join(filter(None, (chunk.heading_path, chunk.content)))
                        for chunk in chunks
                    ]
                    if lease is not None:
                        lease()
                    embeddings = embedder.embed(texts)
                    if len(embeddings) != len(chunks):
                        raise RuntimeError("Embedding 返回数量与子块数量不一致")
                    vector_rows = []
                    keyword_rows = []
                    for chunk, vector in zip(chunks, embeddings, strict=True):
                        if len(vector) != self.settings.embedding_dim:
                            raise RuntimeError(
                                "Embedding 返回维度与 EMBEDDING_DIM 不一致"
                            )
                        metadata = {
                            "chunk_id": chunk.id,
                            "parent_id": chunk.parent_id,
                            "parent_index": chunk.parent_index,
                            "child_order": chunk.child_order,
                            "document_id": chunk.document_id,
                            "version": chunk.version,
                            "chunk_index": chunk.chunk_index,
                            "file_type": chunk.file_type,
                        }
                        vector_rows.append(dict(metadata, vector=vector))
                        keyword_rows.append(
                            dict(
                                metadata,
                                chunk_level="CHILD",
                                document_name=document.name,
                                content=chunk.content,
                                heading_path=chunk.heading_path,
                                locator=chunk.locator,
                                page_start=chunk.page_start,
                                page_end=chunk.page_end,
                                token_count=chunk.token_count,
                                created_at=chunk.created_at,
                            )
                        )
                    if lease is not None:
                        lease()
                    vectors.upsert(vector_rows)
                    keyword_pending.extend(keyword_rows)
                    count += len(chunks)
                    for chunk in chunks:
                        if len(first_ids) < 5:
                            first_ids.append(chunk.id)
                        last_ids.append(chunk.id)
                    if lease is not None:
                        lease()
                    while len(keyword_pending) >= self.settings.es_bulk_size:
                        batch = keyword_pending[: self.settings.es_bulk_size]
                        if lease is not None:
                            lease()
                        keywords.upsert(batch)
                        del keyword_pending[: self.settings.es_bulk_size]
                if not count:
                    raise RuntimeError("没有可索引的子块")
                if keyword_pending:
                    if lease is not None:
                        lease()
                    keywords.upsert(keyword_pending)
                vectors.flush()
                keywords.flush()
                sample_ids = list(dict.fromkeys([*first_ids, *last_ids]))
                self._verify(document.id, version, count, sample_ids, vectors, keywords)
                if heartbeat is not None:
                    heartbeat(count, session.expected_count, last_ids[-1])
                vectors.prune_older(document.id, version)
                keywords.prune_older(document.id, version)
                session.finish(self.settings.embedding_model, count)
        return True

    def audit(self, document: Document, version: int) -> bool:
        """只读巡检已发布版本；过期版本不访问外部索引。"""
        with chunk_orm.index_current_version(document.id, version) as session:
            if session is None:
                return False
            session.validate_children()
            first_ids = []
            last_ids = deque(maxlen=5)
            count = 0
            for chunks in session.iter_children(self.settings.indexing_batch_size):
                count += len(chunks)
                for chunk in chunks:
                    if len(first_ids) < 5:
                        first_ids.append(chunk.id)
                    last_ids.append(chunk.id)
            if not count or count != session.expected_count:
                raise RuntimeError("MySQL 文档子块计数不一致")
            sample_ids = list(dict.fromkeys([*first_ids, *last_ids]))
            with ExitStack() as stack:
                vectors = self.vectors or stack.enter_context(MilvusChunkStore(self.settings))
                keywords = self.keywords or stack.enter_context(
                    ElasticsearchChunkStore(self.settings)
                )
                self._verify(document.id, version, count, sample_ids, vectors, keywords)
        return True

    @staticmethod
    def _verify(document_id, version, count, sample_ids, vectors, keywords) -> None:
        vector_count = vectors.count_version(document_id, version)
        keyword_count = keywords.count_version(document_id, version)
        if vector_count != count or keyword_count != count:
            raise RuntimeError(
                "索引子块数量不一致："
                f"MySQL={count}, Milvus={vector_count}, Elasticsearch={keyword_count}"
            )
        if not vectors.contains(document_id, version, sample_ids) or not keywords.contains(
            document_id, version, sample_ids
        ):
            raise RuntimeError("Milvus、Elasticsearch 抽样子块与 MySQL 不一致")
