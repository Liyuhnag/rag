# -*- coding: utf-8 -*-
"""纯文本解析器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser


class TxtParser(BaseParser):
    """读取纯文本并生成内容块。"""

    file_types = ("txt",)

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raise FeatureNotReady("TxtParser 尚未实现")
