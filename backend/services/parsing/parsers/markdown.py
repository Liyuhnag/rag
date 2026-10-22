# -*- coding: utf-8 -*-
"""Markdown 解析器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser


class MarkdownParser(BaseParser):
    """保留标题、列表、表格和代码块。"""

    file_types = ("md", "markdown")

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raise FeatureNotReady("MarkdownParser 尚未实现")
