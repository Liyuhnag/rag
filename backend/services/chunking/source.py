# -*- coding: utf-8 -*-
"""直接消费对象存储响应流，按需恢复已发布的解析块。"""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from io import TextIOBase
from io import TextIOWrapper
from pathlib import Path

from model.document.model import Document
from services.chunking.common.errors import ChunkingFailed
from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument

_READ_SIZE = 65536
_MAX_RECORD = 4 * 1024 * 1024


class StoredParsedSource:
    """从 S3 响应流增量解码；上下文退出即关闭连接。"""

    def __init__(self, reader=None):
        self.reader = reader or StorageObjectReader()

    @contextmanager
    def read(
        self,
        document: Document,
        version: int,
        md_key: str,
        json_key: str,
    ) -> Iterator[ParsedDocument]:
        if not md_key or not json_key:
            raise ChunkingFailed("解析产物缺失，无法切分")
        request = ParseRequest(
            document.id,
            version,
            document.object_key,
            document.file_type,
            document.name,
            document.size_bytes,
        )
        with self._blocks(md_key, json_key) as blocks:
            try:
                yield ParsedDocument(request, Path(document.name).stem, blocks)
            finally:
                blocks.close()

    @contextmanager
    def _blocks(self, md_key: str, json_key: str):
        with self.reader.open_object(json_key) as body:
            with TextIOWrapper(body, encoding="utf-8") as stream:
                prefix = stream.read(_READ_SIZE)
                if re.search(r'"blocks"\s*:', prefix):
                    yield _local_blocks(stream, prefix)
                    return
        # MinerU 合并 JSON 只有分段索引，正文来自合并 Markdown。
        with self.reader.open_object(md_key) as body:
            with TextIOWrapper(body, encoding="utf-8") as stream:
                yield _markdown_blocks(stream)


def _local_blocks(stream: TextIOBase, prefix: str) -> Iterator[ParsedBlock]:
    for item in _array_items(stream, prefix):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ChunkingFailed("解析块格式无效")
        yield ParsedBlock(
            kind=item.get("kind", "text"),
            text=item["text"],
            page=item.get("page"),
            sheet=item.get("sheet"),
            locator=item.get("locator", ""),
            heading_path=item.get("heading_path", ""),
            metadata=item.get("metadata", {}),
        )


def _array_items(stream: TextIOBase, prefix: str):
    """保留单条记录的有界缓冲，校验 JSON 分隔符与尾部。"""
    decoder = json.JSONDecoder()
    buffer = prefix
    while True:
        match = re.search(r'"blocks"\s*:\s*\[', buffer)
        if match:
            header = buffer[: match.end() - 1]
            buffer = buffer[match.end() :]
            break
        extra = stream.read(_READ_SIZE)
        if not extra or len(buffer) + len(extra) > _MAX_RECORD:
            raise ChunkingFailed("解析产物没有块数组")
        buffer += extra
    first = True
    while True:
        buffer = buffer.lstrip()
        while not buffer:
            buffer = stream.read(_READ_SIZE).lstrip()
            if not buffer:
                raise ChunkingFailed("解析块数组未结束")
        if buffer.startswith("]"):
            tail_parts = [buffer[1:]]
            size = len(buffer) - 1
            while extra := stream.read(_READ_SIZE):
                size += len(extra)
                if size > _MAX_RECORD:
                    raise ChunkingFailed("解析产物尾部过大")
                tail_parts.append(extra)
            try:
                json.loads(header + "[]" + "".join(tail_parts))
            except json.JSONDecodeError as exc:
                raise ChunkingFailed("解析产物 JSON 不完整") from exc
            return
        if not first:
            if not buffer.startswith(","):
                raise ChunkingFailed("解析块之间缺少分隔符")
            buffer = buffer[1:].lstrip()
        while True:
            try:
                item, end = decoder.raw_decode(buffer)
                break
            except json.JSONDecodeError as exc:
                extra = stream.read(_READ_SIZE)
                if not extra or len(buffer) + len(extra) > _MAX_RECORD:
                    raise ChunkingFailed("解析块损坏或超过 4Mi 字符") from exc
                buffer += extra
        yield item
        first = False
        buffer = buffer[end:]


def _markdown_blocks(stream: TextIOBase) -> Iterator[ParsedBlock]:
    page = None
    while text := stream.readline(_READ_SIZE):
        marker = re.fullmatch(r"\s*<!--\s*page:\s*(\d+)\s*-->\s*", text)
        if marker:
            page = int(marker.group(1))
            continue
        yield ParsedBlock(
            kind="text",
            text=text,
            page=page,
            locator=f"page:{page}" if page is not None else "",
            metadata={"verbatim": True},
        )
