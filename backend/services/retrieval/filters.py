# -*- coding: utf-8 -*-
"""检索入口和文档检索共用的查询约束。"""

from dataclasses import dataclass

DEFAULT_MAX_QUERY_LENGTH = 512


@dataclass(frozen=True)
class PageRange:
    """按页码区间过滤子块。"""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start <= 0 or self.end <= 0 or self.start > self.end:
            raise ValueError("页码范围必须满足 1 <= start <= end")
