# -*- coding: utf-8 -*-
"""父子块切分器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.models import ChunkOptions
from services.parsing.models import DocumentChunk
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
