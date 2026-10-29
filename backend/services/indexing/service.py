# -*- coding: utf-8 -*-
"""在版本锁内重放子块，双索引确认成功后发布 READY。"""

from contextlib import ExitStack

from config import load_settings
from model.document.model import Document
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

    def run(self, document: Document, version: int, heartbeat=None) -> bool:
        """过期版本不触碰外部索引；异常保留原始块以供重放。"""
        with chunk_orm.index_current_version(document.id, version) as session:
            if session is None:
                return False
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
                vectors.delete_version(document.id, version)
                keywords.delete_version(document.id, version)
                count = 0
                for chunks in session.iter_children(self.settings.indexing_batch_size):
                    texts = [
                        "\n".join(filter(None, (chunk.heading_path, chunk.content)))
                        for chunk in chunks
                    ]
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
                    vectors.upsert(vector_rows)
                    keywords.upsert(keyword_rows)
                    count += len(chunks)
                    if heartbeat is not None:
                        heartbeat()
                if not count:
                    raise RuntimeError("没有可索引的子块")
                vectors.flush()
                keywords.flush()
                vectors.prune_older(document.id, version)
                keywords.prune_older(document.id, version)
                session.finish(self.settings.embedding_model, count)
        return True
