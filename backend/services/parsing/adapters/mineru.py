# -*- coding: utf-8 -*-
"""MinerU 适配器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument


class MinerUClient:
    """封装 MinerU 的提交、轮询和结果下载。"""

    def parse_pdf(self, source: ParseInput) -> ParsedDocument:
        """提交 PDF 并返回解析结果。"""
        raise FeatureNotReady("MinerUClient.parse_pdf 尚未实现")

    def parse_docx(self, source: ParseInput) -> ParsedDocument:
        """提交 DOCX 并返回解析结果。"""
        raise FeatureNotReady("MinerUClient.parse_docx 尚未实现")
