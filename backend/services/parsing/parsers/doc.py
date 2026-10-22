# -*- coding: utf-8 -*-
"""DOC 解析器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser


class DocParser(BaseParser):
    """旧 Word 文档交给 MinerU 解析。"""

    file_types = ("doc",)

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raise FeatureNotReady("DocParser 尚未实现")
