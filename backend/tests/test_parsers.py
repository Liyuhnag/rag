# -*- coding: utf-8 -*-
"""解析器注册表。"""

import pytest

from services.common.exceptions import FeatureNotReady
from services.parsing.errors import UnsupportedFileType
from services.parsing.models import ParseInput
from services.parsing.models import ParseRequest
from services.parsing.parsers import build_default_registry


def test_default_registry_maps_file_types():
    registry = build_default_registry()

    assert type(registry.get("pdf")).__name__ == "PdfParser"
    assert type(registry.get("docx")).__name__ == "DocxParser"
    assert type(registry.get("xlsx")).__name__ == "XlsxParser"
    assert type(registry.get("csv")).__name__ == "CsvParser"
    assert type(registry.get("txt")).__name__ == "TxtParser"
    assert type(registry.get("md")).__name__ == "MarkdownParser"
    assert type(registry.get("markdown")).__name__ == "MarkdownParser"


def test_unknown_file_type_is_rejected():
    registry = build_default_registry()

    with pytest.raises(UnsupportedFileType):
        registry.get("pptx")


def test_parser_is_scaffold_only():
    registry = build_default_registry()
    source = ParseInput(
        request=ParseRequest(
            document_id="doc-1",
            version=1,
            object_key="documents/doc-1/source/demo.pdf",
            file_type="pdf",
            filename="demo.pdf",
            size_bytes=10,
        ),
    )

    with pytest.raises(FeatureNotReady):
        registry.get("pdf").parse(source)
