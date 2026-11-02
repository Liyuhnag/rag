# -*- coding: utf-8 -*-
"""文档解析服务。"""

from services.indexing.parsing.models import ParseInput
from services.indexing.parsing.models import ParseRequest
from services.indexing.parsing.models import ParsedBlock
from services.indexing.parsing.models import ParsedDocument
from services.indexing.parsing.parsers.registry import ParserRegistry
from services.indexing.parsing.parsers.registry import build_default_registry
from services.indexing.parsing.service import DocumentParsingService

__all__ = [
    "DocumentParsingService",
    "ParseInput",
    "ParserRegistry",
    "build_default_registry",
    "ParseRequest",
    "ParsedBlock",
    "ParsedDocument",
]
