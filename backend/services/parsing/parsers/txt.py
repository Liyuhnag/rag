# -*- coding: utf-8 -*-
"""纯文本解析器。"""

import re

from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.errors import ParseFailed
from services.parsing.models import ParseInput
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser
from services.parsing.ports import ObjectReader

_PARAGRAPH = re.compile(r"\n\s*\n")


class TxtParser(BaseParser):
    """标题作为一级标题，正文按空行保留段落。"""

    file_types = ("txt",)

    def __init__(self, reader: ObjectReader | None = None) -> None:
        self._reader = reader or StorageObjectReader()

    def _parse(self, source: ParseInput) -> ParsedDocument:
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
    blocks = [
        ParsedBlock(kind="text", text=f"# {title}", heading_path=title)
    ]
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
