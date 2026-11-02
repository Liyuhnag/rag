# -*- coding: utf-8 -*-
"""MinerU 分段产物合并服务。"""

from services.indexing.merge.service import DocumentMergeService
from services.indexing.merge.service import all_downloaded
from services.indexing.merge.service import run_merge

__all__ = [
    "DocumentMergeService",
    "all_downloaded",
    "run_merge",
]
