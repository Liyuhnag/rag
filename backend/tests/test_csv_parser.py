# -*- coding: utf-8 -*-
"""CSV 转 JSON。"""

import json

import pytest

from services.parsing.errors import ParseFailed
from services.parsing.models import ParseInput
from services.parsing.models import ParseRequest
from services.parsing.parsers.csv import CsvParser


class _Reader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.keys: list[str] = []

    def read_object(self, key: str) -> bytes:
        self.keys.append(key)
        return self.content

    def write_object(self, key: str, content: bytes, content_type: str) -> None:
        raise AssertionError("CSV 解析不应写入对象")


def _source() -> ParseInput:
    return ParseInput(
        request=ParseRequest(
            document_id="doc-1",
            version=1,
            object_key="documents/doc-1/销售.csv",
            file_type="csv",
            filename="销售.csv",
            size_bytes=32,
        ),
    )


def _records(content: bytes, **limits: int) -> list[dict[str, object]]:
    parsed = CsvParser(_Reader(content), **limits).parse(_source())
    payload = json.loads(parsed.blocks[0].text)
    assert parsed.blocks[0].kind == "table"
    assert isinstance(payload, list)
    return payload


def test_csv_converts_rows_to_json():
    content = "日期,区域,金额\n2026-01-01,华东,100\n2026-01-02,,12.5\n".encode()

    assert _records(content) == [
        {"日期": "2026-01-01", "区域": "华东", "金额": 100},
        {"日期": "2026-01-02", "区域": None, "金额": 12.5},
    ]


def test_csv_reads_gbk_and_semicolon():
    content = "日期;金额\n2026-01-01;80\n".encode("gbk")

    assert _records(content) == [{"日期": "2026-01-01", "金额": 80}]


def test_csv_renames_blank_and_duplicate_headers():
    content = "日期,,日期\n2026-01-01,华东,1\n".encode()

    assert _records(content) == [
        {"日期": "2026-01-01", "column_2": "华东", "column_3": 1}
    ]


def test_csv_keeps_quoted_comma():
    content = '"日期","备注"\n"2026-01-01","含,逗号"\n'.encode()

    assert _records(content) == [{"日期": "2026-01-01", "备注": "含,逗号"}]


def test_csv_rejects_too_many_rows():
    content = "金额\n1\n2\n".encode()

    with pytest.raises(ParseFailed, match="超过 1 行"):
        _records(content, max_rows=1)


def test_csv_rejects_missing_header():
    with pytest.raises(ParseFailed, match="没有表头"):
        _records(b"\n\n")
