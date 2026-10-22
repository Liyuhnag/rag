# -*- coding: utf-8 -*-
"""LibreOffice 转换适配器。"""

import shutil
import subprocess
from pathlib import Path

from services.common.exceptions import FeatureNotReady
from services.parsing.errors import ParserUnavailable


class LibreOfficeConverter:
    """封装 headless LibreOffice 文档转换。"""

    def to_pdf(self, source_path: Path) -> Path:
        """把 DOC/DOCX 转成 PDF。"""
        raise FeatureNotReady("LibreOfficeConverter.to_pdf 尚未实现")

    def to_xlsx(self, source_path: Path) -> Path:
        """把 XLS 转成 XLSX。"""
        binary = shutil.which("soffice") or shutil.which("libreoffice")
        if binary is None:
            raise ParserUnavailable("未找到 LibreOffice")
        outdir = source_path.parent
        try:
            subprocess.run(
                [
                    binary,
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "xlsx",
                    "--outdir",
                    str(outdir),
                    str(source_path),
                ],
                check=True,
                timeout=300,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ParserUnavailable("LibreOffice 转换 XLS 失败") from exc
        target = outdir / f"{source_path.stem}.xlsx"
        if not target.is_file():
            raise ParserUnavailable("LibreOffice 未生成 XLSX")
        return target
