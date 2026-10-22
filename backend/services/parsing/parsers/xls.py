# -*- coding: utf-8 -*-
"""XLS 解析器。"""

import tempfile
from pathlib import Path

from services.parsing.adapters.libreoffice import LibreOfficeConverter
from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser
from services.parsing.parsers.xlsx import XlsxParser
from services.parsing.ports import ObjectReader


class XlsParser(BaseParser):
    """旧 Excel 文档先转 XLSX，再按工作表转成 JSON。"""

    file_types = ("xls",)

    def __init__(
        self,
        converter: LibreOfficeConverter | None = None,
        xlsx_parser: XlsxParser | None = None,
        reader: ObjectReader | None = None,
    ) -> None:
        self._converter = converter or LibreOfficeConverter()
        self._reader = reader or StorageObjectReader()
        self._xlsx_parser = xlsx_parser or XlsxParser()

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raw = self._reader.read_object(source.request.object_key)
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "source.xls"
            source_path.write_bytes(raw)
            converted = self._converter.to_xlsx(source_path)
            content = Path(converted).read_bytes()
        return self._xlsx_parser.parse_bytes(content, source)
