# -*- coding: utf-8 -*-
"""编排解析产物读取、父子块生成和版本化存储。"""

from collections.abc import Iterable
from collections.abc import Iterator

from model.document_chunk import orm as chunk_orm
from model.document_chunk.model import DocumentChunk
from services.indexing.chunking.common.errors import ChunkingFailed
from services.indexing.chunking.common.models import ChunkOptions
from services.indexing.chunking.source import StoredParsedSource
from services.indexing.chunking.splitter import TokenChunker


class DocumentChunkingService:
    """切分主流程：S3 产物 → LlamaIndex 父子节点 → 业务块 → 版本化落库。"""

    def __init__(self, source=None, chunker=None, options=None):
        self.source = source or StoredParsedSource()
        self.chunker = chunker or TokenChunker()
        self.options = options or ChunkOptions()

    def compute_chunks(self, document, version, md_key, json_key, heartbeat=None):
        """锁外切分：读取 S3 并在内存生成块列表；版本已过期返回 None。

        heartbeat(written) 每 200 块调用一次，用于轻量心跳，不持有事务。
        """
        if document.parse_version > version:
            return None
        result = []
        with self.source.read(document, version, md_key, json_key) as parsed:
            chunks = self.chunker.iter_split(parsed, self.options)
            for chunk in _require_chunks(chunks):
                result.append(chunk)
                if heartbeat is not None and len(result) % 200 == 0:
                    heartbeat(len(result))
        return result

    def persist_chunks(self, document_id, version, chunks) -> bool:
        """短事务写库：复核版本、替换旧块并更新计数；False 表示版本过期。"""
        return chunk_orm.replace_current_version(document_id, version, chunks)


def _require_chunks(chunks: Iterable[DocumentChunk]) -> Iterator[DocumentChunk]:
    """空文档不能伪装成成功的切分结果。"""
    found = False
    for chunk in chunks:
        found = True
        yield chunk
    if not found:
        raise ChunkingFailed("解析结果为空，无法生成父子块")
