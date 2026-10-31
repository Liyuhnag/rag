# -*- coding: utf-8 -*-
"""Markdown 原样保留。"""

import pytest

from services.parsing.errors import ParseFailed
from services.parsing.models import ParseInput
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedBlock
from services.parsing.parsers.markdown import MarkdownParser


class _Reader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.keys: list[str] = []

    def read_object(self, key: str) -> bytes:
        self.keys.append(key)
        return self.content

    def write_object(self, key: str, content: bytes, content_type: str) -> None:
        raise AssertionError("Markdown 解析不应写入对象")


def _source(file_type: str = "md", filename: str = "说明.md") -> ParseInput:
    return ParseInput(
        request=ParseRequest(
            document_id="doc-1",
            version=1,
            object_key="documents/doc-1/说明.md",
            file_type=file_type,
            filename=filename,
            size_bytes=12,
        ),
    )


def test_markdown_keeps_source_text():
    text = "# 标题\n\n- 列表\n\n| 列 |\n| --- |\n| 值 |\n\n```python\nprint(1)\n```\n"
    reader = _Reader(text.encode("utf-8"))

    parsed = MarkdownParser(reader).parse(_source())

    assert reader.keys == ["documents/doc-1/说明.md"]
    assert parsed.title == "说明"
    assert parsed.blocks == [ParsedBlock(kind="text", text=text)]


def test_markdown_strips_bom_only():
    reader = _Reader("正文".encode("utf-8-sig"))
    source = _source(filename="readme.markdown", file_type="markdown")

    parsed = MarkdownParser(reader).parse(source)

    assert parsed.title == "readme"
    assert parsed.blocks[0].text == "正文"


def test_markdown_rejects_non_utf8():
    reader = _Reader("中文".encode("gbk"))

    with pytest.raises(ParseFailed, match="UTF-8"):
        MarkdownParser(reader).parse(_source())
