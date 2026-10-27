# -*- coding: utf-8 -*-
"""CSV 解析器。"""

import csv
import io
import json
import re
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.errors import ParseFailed
from services.parsing.models import ParseInput
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument
from services.parsing.parsers._text_stream import file_encoding
from services.parsing.parsers.base import BaseParser
from services.parsing.ports import ObjectReader

_MAX_ROWS = 200_000
_MAX_CELLS = 2_000_000
_DELIMITERS = (",", "\t", ";")
_INTEGER = re.compile(r"-?(0|[1-9]\d*)")
_DECIMAL = re.compile(r"-?(0|[1-9]\d*)\.\d+")


class CsvParser(BaseParser):
    """把每一行转成表头为键的 JSON 对象。"""

    file_types = ("csv",)

    def __init__(
        self,
        reader: ObjectReader | None = None,
        *,
        max_rows: int = _MAX_ROWS,
        max_cells: int = _MAX_CELLS,
    ) -> None:
        self._reader = reader or StorageObjectReader()
        self._max_rows = max_rows
        self._max_cells = max_cells

    def iter_blocks(self, source: ParseInput) -> Iterator[ParsedBlock]:
        """两遍扫描确定动态表头，逐行输出，单记录最多 1Mi 字符。"""
        from services.parsing.parsers._csv_stream import iter_tables

        with TemporaryDirectory(prefix="parse-csv-") as directory:
            path = Path(directory) / "source"
            self._reader.download_object(source.request.object_key, path)
            encoding = file_encoding(path, "CSV", allow_gbk=True)
            yield from iter_tables(
                path,
                encoding,
                _title(source.request.filename),
                self._max_rows,
                self._max_cells,
            )

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raw = self._reader.read_object(source.request.object_key)
        text = _decode(raw)
        delimiter = _detect_delimiter(text)
        title = _title(source.request.filename)
        records, column_count = _read_records(
            text,
            delimiter,
            self._max_rows,
            self._max_cells,
        )
        return ParsedDocument(
            request=source.request,
            title=title,
            blocks=[
                ParsedBlock(
                    kind="table",
                    text=json.dumps(records, ensure_ascii=False),
                    sheet=title,
                    metadata={
                        "delimiter": delimiter,
                        "row_count": len(records),
                        "column_count": column_count,
                    },
                )
            ],
        )


def _decode(raw: bytes) -> str:
    """优先按 UTF-8 读取，失败后再尝试 GBK。"""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseFailed("CSV 不是 UTF-8 或 GBK 文本")


def _detect_delimiter(text: str) -> str:
    """在逗号、制表符和分号中选择表头行出现最多的分隔符。"""
    line = next((item for item in text.splitlines() if item.strip()), "")
    counts = {delimiter: _count_unquoted(line, delimiter) for delimiter in _DELIMITERS}
    chosen = max(counts, key=lambda delimiter: counts[delimiter])
    if counts[chosen] == 0:
        return ","
    return chosen


def _count_unquoted(line: str, delimiter: str) -> int:
    """统计引号外的分隔符数量。"""
    count = 0
    quoted = False
    for char in line:
        if char == '"':
            quoted = not quoted
        elif char == delimiter and not quoted:
            count += 1
    return count


def _read_records(
    text: str,
    delimiter: str,
    max_rows: int,
    max_cells: int,
) -> tuple[list[dict[str, object]], int]:
    """按表头读取记录。超过行数或单元格上限时拒绝。"""
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header_cells = _first_record(reader)
        if header_cells is None:
            raise ParseFailed("CSV 没有表头")
        used: set[str] = set()
        headers = _headers(header_cells, used)
        _check_cells(len(headers), max_cells)
        rows = _rows(reader, headers, used, max_rows, max_cells, len(headers))
    except csv.Error as exc:
        raise ParseFailed("CSV 无法解析") from exc
    return rows, len(headers)


def _first_record(reader: csv.reader) -> list[str] | None:
    """返回第一条非空记录。"""
    for row in reader:
        if not _blank(row):
            return row
    return None


def _rows(
    reader: csv.reader,
    headers: list[str],
    used: set[str],
    max_rows: int,
    max_cells: int,
    cells: int,
) -> list[dict[str, object]]:
    """把数据行转成与表头对齐的记录。"""
    rows: list[dict[str, object]] = []
    for row in reader:
        if _blank(row):
            continue
        if len(rows) >= max_rows:
            raise ParseFailed(f"CSV 超过 {max_rows} 行")
        if len(row) > len(headers):
            _extend_headers(headers, used, len(row), rows)
        width = len(headers)
        cells += width
        _check_cells(cells, max_cells)
        values = [_value(cell) for cell in row]
        values.extend([None] * (width - len(values)))
        rows.append(dict(zip(headers, values, strict=True)))
    return rows


def _headers(cells: list[str], used: set[str]) -> list[str]:
    """空表头和重复表头改成 column_N。"""
    headers: list[str] = []
    for index, cell in enumerate(cells, start=1):
        name = cell.strip()
        if not name or name in used:
            name = _column_name(index, used)
        used.add(name)
        headers.append(name)
    return headers


def _extend_headers(
    headers: list[str],
    used: set[str],
    width: int,
    rows: list[dict[str, object]],
) -> None:
    """数据行比表头更宽时补列表头，已有记录补 null。"""
    while len(headers) < width:
        name = _column_name(len(headers) + 1, used)
        used.add(name)
        headers.append(name)
        for row in rows:
            row[name] = None


def _column_name(index: int, used: set[str]) -> str:
    """生成不与现有表头冲突的列名。"""
    name = f"column_{index}"
    while name in used:
        name = f"{name}_"
    return name


def _value(cell: str) -> object:
    """空单元格转为 null，整数字段保持数字。"""
    text = cell.strip()
    if text == "":
        return None
    if _INTEGER.fullmatch(text):
        return int(text)
    if _DECIMAL.fullmatch(text):
        return float(text)
    return text


def _blank(row: list[str]) -> bool:
    """判断一行是否没有内容。"""
    return all(cell.strip() == "" for cell in row)


def _check_cells(cells: int, max_cells: int) -> None:
    """单元格数量超过上限时拒绝。"""
    if cells > max_cells:
        raise ParseFailed(f"CSV 超过 {max_cells} 个单元格")


def _title(filename: str) -> str:
    """用文件名去掉扩展名作为标题。"""
    name = filename.rsplit("/", 1)[-1]
    stem, dot, _extension = name.rpartition(".")
    return stem if dot else name
