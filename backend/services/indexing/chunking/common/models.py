# -*- coding: utf-8 -*-
"""LlamaIndex 父子层级切分参数。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkOptions:
    """直接对应框架的 chunk_sizes 和 chunk_overlap。"""

    parent_chunk_size: int = 1600
    child_chunk_size: int = 400
    chunk_overlap: int = 80

    def __post_init__(self) -> None:
        if not 0 <= self.chunk_overlap < self.child_chunk_size <= self.parent_chunk_size:
            raise ValueError("必须满足 0 <= overlap < child_chunk_size <= parent_chunk_size")
