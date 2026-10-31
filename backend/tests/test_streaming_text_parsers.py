# -*- coding: utf-8 -*-
"""本地文本解析的有界迭代入口。"""

import json
from pathlib import Path

import pytest

from services.parsing.errors import ParseFailed
from services.parsing.models import ParseInput
from services.parsing.models import ParseRequest
from services.parsing.parsers.csv import CsvParser
from services.parsing.parsers.markdown import MarkdownParser
from services.parsing.parsers.txt import TxtParser


class DownloadReader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.path: Path | None = None

    def download_object(self, key: str, path: Path) -> None:
        self.path = path
        path.write_bytes(self.content)

    def read_object(self, key: str) -> bytes:
        raise AssertionError("迭代解析不得完整读取对象")


def source(kind: str) -> ParseInput:
    return ParseInput(ParseRequest("doc", 1, "source", kind, f"说明.{kind}", 1))


def test_markdown_stream_preserves_long_lines_and_newlines():
    text = "中" * 200_000 + "\r\n尾部\r"
    reader = DownloadReader(text.encode("utf-8-sig"))
    blocks = list(MarkdownParser(reader).iter_blocks(source("md")))
    assert "".join(block.text for block in blocks) == text
    assert max(len(block.text) for block in blocks) <= 65_536
    assert reader.path is not None and not reader.path.exists()


def test_txt_stream_splits_long_paragraph_and_preserves_small_paragraphs():
    text = "第一行\r\n第二行\r\n \r\n" + "甲" * 200_000
    blocks = list(
        TxtParser(DownloadReader(text.encode("gbk"))).iter_blocks(source("txt"))
    )
    assert blocks[0].text == "# 说明"
    assert blocks[1].text == "第一行\n第二行"
    assert "".join(block.text for block in blocks[2:]) == "甲" * 200_000
    assert max(len(block.text) for block in blocks) <= 65_536


def test_stream_cleanup_when_consumer_closes():
    reader = DownloadReader(b"x" * 200_000)
    iterator = MarkdownParser(reader).iter_blocks(source("md"))
    next(iterator)
    iterator.close()
    assert reader.path is not None and not reader.path.exists()


def test_csv_stream_uses_final_headers_and_normalized_values():
    text = '日期,,日期\n2026-01-01,华东,1\n2026-01-02,"多\n行",12.5,额外\n'
    blocks = list(CsvParser(DownloadReader(text.encode())).iter_blocks(source("csv")))
    assert [json.loads(block.text) for block in blocks] == [
        [{"日期": "2026-01-01", "column_2": "华东", "column_3": 1, "column_4": None}],
        [
            {
                "日期": "2026-01-02",
                "column_2": "多\n行",
                "column_3": 12.5,
                "column_4": "额外",
            }
        ],
    ]
    assert all(
        block.kind == "table" and block.metadata["column_count"] == 4
        for block in blocks
    )


@pytest.mark.parametrize("text", ["x" * 1_048_577, 'h\n"' + "\n" * 1_048_577 + '"'])
def test_csv_stream_rejects_unbounded_lines_and_multiline_records(text):
    with pytest.raises(ParseFailed):
        list(CsvParser(DownloadReader(text.encode())).iter_blocks(source("csv")))


def test_csv_stream_validates_limits_before_emitting_rows():
    iterator = CsvParser(DownloadReader(b"a\n1\n2\n"), max_rows=1).iter_blocks(
        source("csv")
    )
    with pytest.raises(ParseFailed, match="超过 1 行"):
        next(iterator)


def test_csv_header_only_emits_empty_table():
    blocks = list(CsvParser(DownloadReader(b"a,b\n")).iter_blocks(source("csv")))
    assert len(blocks) == 1 and json.loads(blocks[0].text) == []
    assert blocks[0].metadata["headers"] == ["a", "b"]


@pytest.mark.parametrize(
    "kind,parser", [("txt", TxtParser), ("csv", CsvParser), ("md", MarkdownParser)]
)
def test_invalid_encoding_cleans_download(kind, parser):
    reader = DownloadReader(b"\xff\xff")
    with pytest.raises(ParseFailed):
        list(parser(reader).iter_blocks(source(kind)))
    assert reader.path is not None and not reader.path.exists()


def test_txt_stream_marks_only_following_fragments_as_continuations():
    text = "甲" * 70_000 + "\n\n新段落"
    blocks = list(TxtParser(DownloadReader(text.encode())).iter_blocks(source("txt")))
    assert blocks[1].metadata == {}
    assert blocks[2].metadata == {"continuation": True}
    assert blocks[3].metadata == {} and blocks[3].text == "新段落"


def test_markdown_stream_marks_fragments_for_verbatim_joining():
    blocks = list(
        MarkdownParser(DownloadReader(b"a" * 70_000)).iter_blocks(source("md"))
    )
    assert all(block.metadata == {"verbatim": True} for block in blocks)


def test_txt_stream_rejects_excessive_whitespace_without_retaining_it():
    reader = DownloadReader(b"a" + b" " * 70_000)
    with pytest.raises(ParseFailed, match="连续空白"):
        list(TxtParser(reader).iter_blocks(source("txt")))
    assert reader.path is not None and not reader.path.exists()


def test_csv_stream_preserves_gbk_semicolon_and_value_rules():
    text = "编号;重复;重复;\n001;12.5;;-1\n"
    blocks = list(
        CsvParser(DownloadReader(text.encode("gbk"))).iter_blocks(source("csv"))
    )
    assert json.loads(blocks[0].text) == [
        {"编号": "001", "重复": 12.5, "column_3": None, "column_4": -1}
    ]


def test_csv_stream_enforces_cell_limit_and_cleans_download():
    reader = DownloadReader(b"a,b\n1,2\n")
    with pytest.raises(ParseFailed, match="超过 3 个单元格"):
        list(CsvParser(reader, max_cells=3).iter_blocks(source("csv")))
    assert reader.path is not None and not reader.path.exists()


def test_csv_stream_cleanup_when_closed_mid_table():
    reader = DownloadReader(b"a\n1\n2\n")
    iterator = CsvParser(reader).iter_blocks(source("csv"))
    next(iterator)
    iterator.close()
    assert reader.path is not None and not reader.path.exists()
