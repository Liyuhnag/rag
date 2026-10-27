# -*- coding: utf-8 -*-
"""CSV 两遍扫描；内存限制为表头和一条有界逻辑记录。"""

import csv
import json
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

from services.parsing.errors import ParseFailed
from services.parsing.models import ParsedBlock
from services.parsing.parsers.csv import _blank
from services.parsing.parsers.csv import _check_cells
from services.parsing.parsers.csv import _column_name
from services.parsing.parsers.csv import _detect_delimiter
from services.parsing.parsers.csv import _first_record
from services.parsing.parsers.csv import _headers
from services.parsing.parsers.csv import _value

MAX_RECORD_CHARS = 1_048_576


class _BoundedLines:
    """csv.reader 可请求多条物理行，但一条逻辑记录总计不得超过 1Mi 字符。"""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.remaining = MAX_RECORD_CHARS

    def __iter__(self) -> "_BoundedLines":
        return self

    def __next__(self) -> str:
        line = self.stream.readline(self.remaining + 1)
        if not line:
            raise StopIteration
        self.remaining -= len(line)
        if self.remaining < 0:
            raise ParseFailed("CSV 单条记录超过 1048576 字符")
        return line


def _records(path: Path, encoding: str, delimiter: str) -> Iterator[list[str]]:
    with path.open(encoding=encoding, newline="") as stream:
        lines = _BoundedLines(stream)
        reader = csv.reader(lines, delimiter=delimiter)
        try:
            while True:
                lines.remaining = MAX_RECORD_CHARS
                try:
                    yield next(reader)
                except StopIteration:
                    return
        except csv.Error as exc:
            raise ParseFailed("CSV 无法解析") from exc


def _delimiter(path: Path, encoding: str) -> str:
    with path.open(encoding=encoding, newline="") as stream:
        for line in _BoundedLines(stream):
            if line.strip():
                return _detect_delimiter(line)
    return ","


def iter_tables(
    path: Path, encoding: str, title: str, max_rows: int, max_cells: int
) -> Iterator[ParsedBlock]:
    """先校验并确定最终表头，再逐行输出单元素 JSON 数组。"""
    delimiter = _delimiter(path, encoding)
    rows = _records(path, encoding, delimiter)
    try:
        header = _first_record(rows)
        if header is None:
            raise ParseFailed("CSV 没有表头")
        used: set[str] = set()
        headers = _headers(header, used)
        cells = len(headers)
        _check_cells(cells, max_cells)
        row_count = 0
        for row in rows:
            if _blank(row):
                continue
            row_count += 1
            if row_count > max_rows:
                raise ParseFailed(f"CSV 超过 {max_rows} 行")
            while len(headers) < len(row):
                name = _column_name(len(headers) + 1, used)
                used.add(name)
                headers.append(name)
            cells += len(headers)
            _check_cells(cells, max_cells)
    finally:
        rows.close()
    metadata = {
        "delimiter": delimiter,
        "row_count": row_count,
        "column_count": len(headers),
    }
    if row_count == 0:
        yield ParsedBlock(kind="table", text="[]", sheet=title, metadata=metadata)
        return
    rows = _records(path, encoding, delimiter)
    try:
        _first_record(rows)
        index = 0
        for row in rows:
            if _blank(row):
                continue
            index += 1
            values = [_value(cell) for cell in row]
            values.extend([None] * (len(headers) - len(values)))
            record = dict(zip(headers, values, strict=True))
            yield ParsedBlock(
                kind="table",
                text=json.dumps([record], ensure_ascii=False),
                sheet=title,
                locator=f"row:{index}",
                metadata=dict(metadata),
            )
    finally:
        rows.close()
