# -*- coding: utf-8 -*-
"""按 token 和标题结构切父子块。"""

from model.document_chunk.model import DocumentChunk
from services.chunking.models import ChunkOptions
from services.common.exceptions import FeatureNotReady
from services.parsing.models import ParsedDocument


class TokenChunker:
    """按 token 和标题结构生成父子块。"""

    def split(
        self,
        parsed: ParsedDocument,
        options: ChunkOptions,
    ) -> list[DocumentChunk]:
        """切分解析结果。"""
        raise FeatureNotReady("TokenChunker 尚未实现")
