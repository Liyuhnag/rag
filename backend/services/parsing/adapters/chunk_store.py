# -*- coding: utf-8 -*-
"""document_chunks 落库适配器。"""

from model.document_chunk import orm as chunk_orm
from model.document_chunk.model import DocumentChunk


class OrmChunkStore:
    """把切分结果写入 MySQL。"""

    def replace_for_version(
        self,
        document_id: str,
        version: int,
        chunks: list[DocumentChunk],
    ) -> None:
        """事务替换指定文档版本的父子块。"""
        chunk_orm.replace_for_version(document_id, version, chunks)
