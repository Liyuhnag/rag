# -*- coding: utf-8 -*-
"""Markdown 解析器。"""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.errors import ParseFailed
from services.parsing.models import ParseInput
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument
from services.parsing.parsers._text_stream import file_encoding
from services.parsing.parsers._text_stream import text_chunks
from services.parsing.parsers.base import BaseParser
from services.parsing.ports import ObjectReader


class MarkdownParser(BaseParser):
    """原样保留 Markdown 文本。"""

    file_types = ("md", "markdown")

    def __init__(self, reader: ObjectReader | None = None) -> None:
        self._reader = reader or StorageObjectReader()

    def iter_blocks(self, source: ParseInput) -> Iterator[ParsedBlock]:
        """至多 64Ki 字符一块；verbatim 碎片必须原样拼接。"""
        with TemporaryDirectory(prefix="parse-markdown-") as directory:
            path = Path(directory) / "source"
            self._reader.download_object(source.request.object_key, path)
            encoding = file_encoding(path, "Markdown", allow_gbk=False)
            for text in text_chunks(path, encoding):
                yield ParsedBlock(kind="text", text=text, metadata={"verbatim": True})

    def _parse(self, source: ParseInput) -> ParsedDocument:
        """兼容旧小文件调用；大文件调用应使用 iter_blocks。"""
        raw = self._reader.read_object(source.request.object_key)
        return ParsedDocument(
            request=source.request,
            title=_title(source.request.filename),
            blocks=[ParsedBlock(kind="text", text=_decode(raw))],
        )


def _decode(raw: bytes) -> str:
    """按 UTF-8 读取文本，并去掉文件开头的 BOM。"""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseFailed("Markdown 不是 UTF-8 文本") from exc


def _title(filename: str) -> str:
    """用文件名去掉扩展名作为标题。"""
    name = filename.rsplit("/", 1)[-1]
    stem, dot, _extension = name.rpartition(".")
    return stem if dot else name
