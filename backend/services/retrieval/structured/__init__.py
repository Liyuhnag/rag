# -*- coding: utf-8 -*-
"""结构化表查询。"""

from services.retrieval.structured.models import StructuredQueryRequest
from services.retrieval.structured.models import StructuredQueryResult
from services.retrieval.structured.service import StructuredQueryService

__all__ = [
    "StructuredQueryRequest",
    "StructuredQueryResult",
    "StructuredQueryService",
]
