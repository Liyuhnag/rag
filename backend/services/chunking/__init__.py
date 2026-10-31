# -*- coding: utf-8 -*-
"""父子块切分。

输入是解析完成的文档，输出是父子块。这里不读取源文件，也不调用解析器。
"""

from services.chunking.common.models import ChunkOptions
from services.chunking.token import TokenChunker

__all__ = [
    "ChunkOptions",
    "TokenChunker",
]
