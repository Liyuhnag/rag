# -*- coding: utf-8 -*-
"""解析产物跨读取缓冲及 MinerU 页码恢复。"""

import json
from contextlib import contextmanager
from io import BytesIO
from io import RawIOBase
from types import SimpleNamespace

import pytest

from services.chunking.source import StoredParsedSource
from services.chunking.common.errors import ChunkingFailed


def _document():
    return SimpleNamespace(
        id="doc",
        name="a.pdf",
        file_type="pdf",
        object_key="source",
        size_bytes=10,
    )


class Reader:
    def __init__(self, payload, markdown=""):
        self.payload = payload
        self.markdown = markdown

    @contextmanager
    def open_object(self, key):
        text = self.payload if key == "json" else self.markdown
        with BytesIO(text.encode("utf-8")) as stream:
            yield stream


def test_first_block_arrives_before_end_and_early_exit_closes_stream():
    payload = json.dumps(
        {
            "blocks": [
                {"text": "首块"},
                {"text": "后续正文" * 100000},
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")

    class LimitedStream(BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 65536
            return super().read(size)

    stream = LimitedStream(payload)

    class StreamingReader:
        @contextmanager
        def open_object(self, key):
            with stream:
                yield stream

    with StoredParsedSource(StreamingReader()).read(
        _document(),
        1,
        "md",
        "json",
    ) as parsed:
        assert next(iter(parsed.blocks)).text == "首块"
        assert stream.tell() < len(payload)
    assert stream.closed


def test_utf8_characters_split_across_network_reads():
    payload = json.dumps(
        {"blocks": [{"text": "中文🙂跨网络包"}]},
        ensure_ascii=False,
    ).encode("utf-8")

    class FragmentedBody(RawIOBase):
        def __init__(self):
            self.source = BytesIO(payload)

        def readable(self):
            return True

        def readinto(self, buffer):
            data = self.source.read(min(len(buffer), 2))
            buffer[: len(data)] = data
            return len(data)

    body = FragmentedBody()

    class StreamingReader:
        @contextmanager
        def open_object(self, key):
            with body:
                yield body

    with StoredParsedSource(StreamingReader()).read(
        _document(),
        1,
        "md",
        "json",
    ) as parsed:
        assert [block.text for block in parsed.blocks] == ["中文🙂跨网络包"]
    assert body.closed


def test_local_json_blocks_cross_buffer_without_losing_text():
    text = "正文" * 40000
    reader = Reader(json.dumps({"blocks": [{"kind": "text", "text": text}]}))
    with StoredParsedSource(reader).read(_document(), 1, "md", "json") as parsed:
        assert [block.text for block in parsed.blocks] == [text]


def test_mineru_markdown_page_markers_are_not_chunk_content():
    reader = Reader('{"parts": []}', "<!-- page: 7 -->\n# 标题\n正文\n")
    with StoredParsedSource(reader).read(_document(), 1, "md", "json") as parsed:
        blocks = list(parsed.blocks)
    assert "".join(block.text for block in blocks) == "# 标题\n正文\n"
    assert all(block.page == 7 for block in blocks)


@pytest.mark.parametrize(
    "payload",
    [
        '{"blocks": [{"text": "one"}{"text": "two"}]}',
        '{"blocks": [{"text": "one"}]',
        '{"blocks": [{"text": "one"}]}garbage',
    ],
)
def test_invalid_json_framing_fails(payload):
    with StoredParsedSource(Reader(payload)).read(
        _document(),
        1,
        "md",
        "json",
    ) as parsed:
        with pytest.raises(ChunkingFailed):
            list(parsed.blocks)


def test_truncated_local_block_fails_explicitly():
    reader = Reader('{"blocks": [{"text": "broken')
    with StoredParsedSource(reader).read(_document(), 1, "md", "json") as parsed:
        with pytest.raises(ChunkingFailed):
            list(parsed.blocks)
