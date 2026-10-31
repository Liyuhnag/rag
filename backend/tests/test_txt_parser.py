# -*- coding: utf-8 -*-
"""TXT 按空行保留段落。"""

import pytest

from services.parsing.errors import ParseFailed
from services.parsing.models import ParseInput
from services.parsing.models import ParseRequest
from services.parsing.parsers.txt import TxtParser


class _Reader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.keys: list[str] = []

    def read_object(self, key: str) -> bytes:
        self.keys.append(key)
        return self.content

    def write_object(self, key: str, content: bytes, content_type: str) -> None:
        raise AssertionError("TXT 解析不应写入对象")


def _source() -> ParseInput:
    return ParseInput(
        request=ParseRequest(
            document_id="doc-1",
            version=1,
            object_key="documents/doc-1/说明.txt",
            file_type="txt",
            filename="说明.txt",
            size_bytes=12,
        ),
    )


def test_txt_splits_paragraphs_on_blank_lines():
    text = "第一段\n仍在第一段\n\n第二段\n\n\n第三段"
    parsed = TxtParser(_Reader(text.encode())).parse(_source())

    assert parsed.title == "说明"
    assert [block.text for block in parsed.blocks] == [
        "# 说明",
        "第一段\n仍在第一段",
        "第二段",
        "第三段",
    ]
    assert [block.heading_path for block in parsed.blocks] == ["说明"] * 4


def test_txt_reads_gbk_and_strips_bom():
    parsed = TxtParser(_Reader("正文".encode("utf-8-sig"))).parse(_source())

    assert [block.text for block in parsed.blocks] == ["# 说明", "正文"]

    parsed = TxtParser(_Reader("甲段\n\n乙段".encode("gbk"))).parse(_source())

    assert [block.text for block in parsed.blocks] == ["# 说明", "甲段", "乙段"]


def test_txt_rejects_non_text_encoding():
    with pytest.raises(ParseFailed, match="UTF-8 或 GBK"):
        TxtParser(_Reader(bytes(range(128, 160)))).parse(_source())
