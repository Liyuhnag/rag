# -*- coding: utf-8 -*-
"""LibreOffice 转换适配器。"""

from pathlib import Path

from services.common.exceptions import FeatureNotReady


class LibreOfficeConverter:
    """封装 headless LibreOffice 文档转换。"""

    def to_pdf(self, source_path: Path) -> Path:
        """把 DOC/DOCX 转成 PDF。"""
        raise FeatureNotReady("LibreOfficeConverter.to_pdf 尚未实现")

    def to_xlsx(self, source_path: Path) -> Path:
        """把 XLS 转成 XLSX。"""
        raise FeatureNotReady("LibreOfficeConverter.to_xlsx 尚未实现")
