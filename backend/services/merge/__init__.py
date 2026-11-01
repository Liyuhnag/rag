# -*- coding: utf-8 -*-
"""MinerU 分段产物合并服务。"""

from services.merge.service import DocumentMergeService
from services.merge.service import all_downloaded
from services.merge.service import run_merge

__all__ = [
    "DocumentMergeService",
    "all_downloaded",
    "run_merge",
]
