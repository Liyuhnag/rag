# -*- coding: utf-8 -*-
"""纯文本解析器。"""

import re
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.errors import ParseFailed
from services.parsing.models import ParseInput
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument
from services.parsing.parsers._text_stream import TEXT_CHARS
from services.parsing.parsers._text_stream import file_encoding
from services.parsing.parsers._text_stream import text_chunks
from services.parsing.parsers.base import BaseParser
from services.parsing.ports import ObjectReader

_PARAGRAPH = re.compile(r"\n\s*\n")


class TxtParser(BaseParser):
    """标题作为一级标题，正文按空行保留段落。"""

    file_types = ("txt",)

    def __init__(self, reader: ObjectReader | None = None) -> None:
        self._reader = reader or StorageObjectReader()

    def iter_blocks(self, source: ParseInput) -> Iterator[ParsedBlock]:
        """按段输出；超长段落拆为至多 64Ki 字符的连续碎片。"""
        with TemporaryDirectory(prefix="parse-txt-") as directory:
            path = Path(directory) / "source"
            self._reader.download_object(source.request.object_key, path)
            encoding = file_encoding(path, "TXT", allow_gbk=True)
            title = _title(source.request.filename)
            yield ParsedBlock(kind="text", text=f"# {title}", heading_path=title)
            yield from _iter_paragraphs(path, encoding, title)

    def _parse(self, source: ParseInput) -> ParsedDocument:
        """兼容旧小文件调用；大文件调用应使用 iter_blocks。"""
        raw = self._reader.read_object(source.request.object_key)
        title = _title(source.request.filename)
        return ParsedDocument(
            request=source.request,
            title=title,
            blocks=_blocks(title, _paragraphs(_decode(raw))),
        )


def _decode(raw: bytes) -> str:
    """优先按 UTF-8 读取，失败后再尝试 GBK。"""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseFailed("TXT 不是 UTF-8 或 GBK 文本")


def _blocks(title: str, paragraphs: list[str]) -> list[ParsedBlock]:
    """一级标题在前，其后每个段落一块。"""
    blocks = [ParsedBlock(kind="text", text=f"# {title}", heading_path=title)]
    blocks.extend(
        ParsedBlock(kind="text", text=paragraph, heading_path=title)
        for paragraph in paragraphs
    )
    return blocks


def _paragraphs(text: str) -> list[str]:
    """用空行切开段落，并去掉每段首尾空白。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    return [
        paragraph.strip()
        for paragraph in _PARAGRAPH.split(normalized)
        if paragraph.strip()
    ]


def _title(filename: str) -> str:
    """用文件名去掉扩展名作为标题。"""
    name = filename.rsplit("/", 1)[-1]
    stem, dot, _extension = name.rpartition(".")
    return stem if dot else name


def _iter_paragraphs(path: Path, encoding: str, title: str) -> Iterator[ParsedBlock]:
    """暂存一块正文和有限空白；超过 64Ki 连续空白明确拒绝。"""
    chars: list[str] = []
    whitespace: list[str] = []
    newlines = 0
    continuation = False
    for chunk in text_chunks(path, encoding, normalize=True):
        for char in chunk:
            if char.isspace():
                whitespace.append(char)
                newlines += char == "\n"
                if len(whitespace) > TEXT_CHARS:
                    raise ParseFailed("TXT 连续空白超过 65536 字符")
                continue
            if newlines >= 2:
                if chars:
                    yield _paragraph_block(chars, title, continuation)
                chars = []
                continuation = False
            elif chars or continuation:
                for space in whitespace:
                    chars.append(space)
                    if len(chars) == TEXT_CHARS:
                        yield _paragraph_block(chars, title, continuation)
                        chars = []
                        continuation = True
            whitespace = []
            newlines = 0
            chars.append(char)
            if len(chars) == TEXT_CHARS:
                yield _paragraph_block(chars, title, continuation)
                chars = []
                continuation = True
    if chars:
        yield _paragraph_block(chars, title, continuation)


def _paragraph_block(chars: list[str], title: str, continuation: bool) -> ParsedBlock:
    metadata = {"continuation": True} if continuation else {}
    return ParsedBlock(
        kind="text", text="".join(chars), heading_path=title, metadata=metadata
    )
