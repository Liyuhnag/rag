# -*- coding: utf-8 -*-
"""解析模块的外部服务适配器。"""

from services.parsing.adapters.mineru import MinerUClient
from services.parsing.adapters.object_reader import StorageObjectReader

__all__ = [
    "MinerUClient",
    "StorageObjectReader",
]
