# -*- coding: utf-8 -*-
"""Disk-backed XLSX reader; no openpyxl workbook/shared-string materialization.

ZIP directory uses O(entry count), bounded by preflight count and byte limits. XML text,
styles, cell text and output batches have explicit limits. SQLite stores shared
strings, cells and merge ranges with a fixed page cache. Temporary disk usage
still scales with workbook size. A worksheet is scanned before emitting blocks
so late columns can backfill earlier records without retaining them in RAM.
"""

import json
import posixpath
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from openpyxl.styles.numbers import BUILTIN_FORMATS
from openpyxl.styles.numbers import is_date_format
from openpyxl.styles.numbers import is_timedelta_format
from openpyxl.utils.cell import coordinate_to_tuple
from openpyxl.utils.cell import range_boundaries
from openpyxl.utils.datetime import CALENDAR_MAC_1904
from openpyxl.utils.datetime import CALENDAR_WINDOWS_1900
from openpyxl.utils.datetime import from_excel
from openpyxl.utils.datetime import from_ISO8601

from services.parsing.archive import check_archive_directory
from services.parsing.errors import ParseFailed
from services.parsing.models import ParsedBlock

_MAX_TEXT = 1024 * 1024
_MAX_FILES = 10000
_MAX_STYLES = 65536
_BATCH_CHARS = 1024 * 1024


class _LimitedXML:
    """Reject oversized text/attribute runs before ElementTree accumulates them."""

    def __init__(self, stream):
        self.stream = stream
        self.run = 0
        self.tail = b""

    def read(self, size=-1):
        data = self.stream.read(min(size if size > 0 else 16384, 16384))
        scanned = (self.tail + data).upper()
        if b"<!DOCTYPE" in scanned or b"<!ENTITY" in scanned or b"\x00" in data:
            raise ParseFailed("Excel 不支持此 XML 声明")
        self.tail = scanned[-16:]
        pieces = data.split(b"<")
        self.run += len(pieces[0])
        if self.run > _MAX_TEXT or any(len(part) > _MAX_TEXT for part in pieces[1:]):
            raise ParseFailed("Excel XML 文本超过单元格大小限制")
        if len(pieces) > 1:
            self.run = len(pieces[-1])
        return data


def _nodes(archive, name, tags):
    """Detach processed subtrees, including rows, to avoid XML tree growth."""
    with archive.open(name) as stream:
        stack = []
        selected = 0
        retained = 0
        for event, node in ET.iterparse(_LimitedXML(stream), events=("start", "end")):
            tag = node.tag.rsplit("}", 1)[-1]
            if event == "start":
                stack.append(node)
                if tag in tags:
                    if not selected:
                        retained = 0
                    selected += 1
                if selected:
                    retained += 64 + sum(
                        len(key) + len(value) for key, value in node.attrib.items()
                    )
                    if retained > 2 * _MAX_TEXT:
                        raise ParseFailed("Excel XML 节点超过大小限制")
                continue
            if selected:
                retained += len(node.text or "") + len(node.tail or "")
                if retained > 2 * _MAX_TEXT:
                    raise ParseFailed("Excel XML 节点超过大小限制")
            if tag in tags:
                yield tag, node
                selected -= 1
            if not selected:
                node.clear()
                if len(stack) > 1:
                    stack[-2].remove(node)
            stack.pop()


def _text(node):
    # Inline cells wrap text in <is>; shared strings use <si> directly.
    # Phonetic <rPh>/<t> annotates existing text and must not be appended.
    if node.tag.rsplit("}", 1)[-1] == "c":
        node = next(
            (child for child in node if child.tag.rsplit("}", 1)[-1] == "is"),
            (),
        )
    parts = []
    for child in node:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "t":
            parts.append(child.text or "")
        elif tag == "r":
            parts.extend(
                item.text or "" for item in child if item.tag.rsplit("}", 1)[-1] == "t"
            )
    text = "".join(parts)
    if len(text) > _MAX_TEXT:
        raise ParseFailed("Excel 单元格文本超过大小限制")
    return text


def _child(node, name):
    for child in node:
        if child.tag.rsplit("}", 1)[-1] == name:
            return child.text
    return None


def _index(archive, db):
    if "xl/sharedStrings.xml" in archive.namelist():
        for index, (_, node) in enumerate(
            _nodes(archive, "xl/sharedStrings.xml", {"si"})
        ):
            db.execute("INSERT INTO strings VALUES (?, ?)", (index, _text(node)))
    if "xl/styles.xml" in archive.namelist():
        formats = {}
        style = 0
        for tag, node in _nodes(archive, "xl/styles.xml", {"numFmt", "cellXfs"}):
            if tag == "numFmt":
                formats[int(node.attrib["numFmtId"])] = node.attrib.get(
                    "formatCode", ""
                )
                if len(formats) > _MAX_STYLES:
                    raise ParseFailed("Excel 样式超过限制")
            else:
                if len(node) > _MAX_STYLES:
                    raise ParseFailed("Excel 样式超过限制")
                for child in node:
                    number = int(child.attrib.get("numFmtId", 0))
                    code = formats.get(number, BUILTIN_FORMATS.get(number, ""))
                    kind = 2 if is_timedelta_format(code) else int(is_date_format(code))
                    db.execute("INSERT INTO styles VALUES (?, ?)", (style, kind))
                    style += 1


def _cell(node, db, epoch, normalize):
    kind = node.attrib.get("t", "n")
    raw = _child(node, "v")
    if kind == "inlineStr":
        value = _text(node)
    elif kind == "s" and raw is not None:
        found = db.execute(
            "SELECT value FROM strings WHERE id=?", (int(raw),)
        ).fetchone()
        if found is None:
            raise ParseFailed("Excel 共享字符串引用无效")
        value = found[0]
    elif raw is None:
        value = None
    elif kind == "b":
        value = bool(int(raw))
    elif kind == "d":
        value = from_ISO8601(raw)
    elif kind in {"str", "e"}:
        value = raw
    else:
        value = float(raw) if any(char in raw for char in ".eE") else int(raw)
        style = db.execute(
            "SELECT kind FROM styles WHERE id=?", (int(node.attrib.get("s", 0)),)
        ).fetchone()
        if style and style[0]:
            value = from_excel(value, epoch, timedelta=style[0] == 2)
    return normalize(value)


def _workbook(archive, max_sheets):
    relations = {}
    for _, node in _nodes(archive, "xl/_rels/workbook.xml.rels", {"Relationship"}):
        if node.attrib.get("TargetMode") == "External":
            continue
        target = node.attrib["Target"]
        path = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
        relations[node.attrib["Id"]] = path
        if len(relations) > _MAX_FILES:
            raise ParseFailed("Excel 文件成员超过限制")
    sheets = []
    epoch = CALENDAR_WINDOWS_1900
    for tag, node in _nodes(archive, "xl/workbook.xml", {"sheet", "workbookPr"}):
        if tag == "workbookPr":
            if node.attrib.get("date1904", "0") in {"1", "true"}:
                epoch = CALENDAR_MAC_1904
        else:
            identifier = node.attrib.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            sheets.append((node.attrib["name"], relations[identifier]))
            if len(sheets) > max_sheets:
                raise ParseFailed(f"Excel 超过 {max_sheets} 个工作表")
    return sheets, epoch


def _load_sheet(archive, path, db, epoch, options, normalize):
    max_row = 0
    max_col = 0
    covered = 0
    for tag, node in _nodes(archive, path, {"mergeCell", "dimension", "row"}):
        if tag == "row":
            row = int(node.attrib.get("r", max_row + 1))
            max_row = max(max_row, row)
            current_col = 0
            for cell in node:
                if cell.tag.rsplit("}", 1)[-1] != "c":
                    continue
                if "r" in cell.attrib:
                    r, col = coordinate_to_tuple(cell.attrib["r"])
                else:
                    r, col = row, current_col + 1
                current_col = col
                max_col = max(max_col, col)
                if col > options._max_columns:
                    raise ParseFailed(f"Excel 超过 {options._max_columns} 列")
                value = _cell(cell, db, epoch, normalize)
                db.execute(
                    "INSERT OR REPLACE INTO cells VALUES (?, ?, ?)",
                    (r, col, json.dumps(value, ensure_ascii=False)),
                )
        elif tag == "mergeCell":
            c1, r1, c2, r2 = range_boundaries(node.attrib["ref"])
            max_row, max_col = max(max_row, r2), max(max_col, c2)
            covered += (r2 - r1 + 1) * (c2 - c1 + 1)
            if covered > options._max_cells:
                raise ParseFailed(f"Excel 超过 {options._max_cells} 个单元格")
            db.execute("INSERT INTO merges VALUES (?, ?, ?, ?)", (r1, r2, c1, c2))
        elif tag == "dimension":
            _, _, col, row = range_boundaries(node.attrib["ref"])
            max_row, max_col = max(max_row, row or 0), max(max_col, col or 0)
        if max_row > options._max_rows + 1:
            raise ParseFailed(f"Excel 超过 {options._max_rows} 行")
        if max_col > options._max_columns:
            raise ParseFailed(f"Excel 超过 {options._max_columns} 列")
    return max_row


def _rows(db, count):
    for row in range(1, count + 1):
        values = {}
        for col, value in db.execute("SELECT col,value FROM cells WHERE row=?", (row,)):
            values[col] = json.loads(value)
        for r1, c1, c2 in db.execute(
            "SELECT r1,c1,c2 FROM merges WHERE r1<=? AND r2>=?", (row, row)
        ):
            found = db.execute(
                "SELECT value FROM cells WHERE row=? AND col=?", (r1, c1)
            ).fetchone()
            value = json.loads(found[0]) if found else None
            for col in range(c1, c2 + 1):
                if values.get(col) is None:
                    values[col] = value
        width = max(
            (col for col, value in values.items() if value is not None), default=0
        )
        yield [values.get(col) for col in range(1, width + 1)]


def _sheet_blocks(db, title, count, options, helpers, cells):
    headers_fn, extend_fn, check_fn = helpers
    headers = []
    used = set()
    records = 0
    for values in _rows(db, count):
        if not values:
            continue
        if not headers:
            headers = headers_fn(values, used)
            cells[0] += len(headers)
            check_fn(cells[0], options._max_cells)
            continue
        if len(values) > len(headers):
            extend_fn(headers, used, len(values), [])
        cells[0] += len(headers)
        check_fn(cells[0], options._max_cells)
        records += 1
        if records > options._max_rows:
            raise ParseFailed(f"Excel 超过 {options._max_rows} 行")
        db.execute(
            "INSERT INTO records VALUES (?, ?)",
            (records, json.dumps(values, ensure_ascii=False)),
        )
    if not headers:
        return
    batch = []
    chars = 0
    for (payload,) in db.execute("SELECT value FROM records ORDER BY id"):
        values = json.loads(payload)
        values.extend([None] * (len(headers) - len(values)))
        record = dict(zip(headers, values, strict=True))
        size = len(json.dumps(record, ensure_ascii=False))
        if size > _BATCH_CHARS:
            raise ParseFailed("Excel 单行文本超过大小限制")
        if batch and (len(batch) >= options._batch_rows or chars + size > _BATCH_CHARS):
            yield _block(title, headers, batch)
            batch, chars = [], 0
        batch.append(record)
        chars += size
    if batch or not records:
        yield _block(title, headers, batch)


def _block(title, headers, batch):
    return ParsedBlock(
        kind="table",
        text=json.dumps(batch, ensure_ascii=False),
        sheet=title,
        metadata={"row_count": len(batch), "column_count": len(headers)},
    )


def iter_xlsx(
    path: Path, db_path: Path, options, normalize, helpers
) -> Iterator[ParsedBlock]:
    check_archive_directory(path, label="Excel", max_files=_MAX_FILES)
    with ZipFile(path) as archive, sqlite3.connect(db_path) as db:
        if len(archive.infolist()) > _MAX_FILES:
            raise ParseFailed("Excel 文件成员超过限制")
        if (
            sum(max(info.file_size, 0) for info in archive.infolist())
            > options._max_uncompressed
        ):
            raise ParseFailed(f"Excel 解压后超过 {options._max_uncompressed} 字节")
        db.executescript("""
            PRAGMA cache_size=-2048;
            PRAGMA temp_store=FILE;
            PRAGMA journal_mode=OFF;
            CREATE TABLE strings(id INTEGER PRIMARY KEY, value TEXT);
            CREATE TABLE styles(id INTEGER PRIMARY KEY, kind INTEGER);
            CREATE TABLE cells(row INTEGER, col INTEGER, value TEXT, PRIMARY KEY(row,col));
            CREATE TABLE merges(r1 INTEGER, r2 INTEGER, c1 INTEGER, c2 INTEGER);
            CREATE INDEX merge_rows ON merges(r1,r2);
            CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT);
        """)
        _index(archive, db)
        sheets, epoch = _workbook(archive, options._max_sheets)
        found = False
        cells = [0]
        for title, member in sheets:
            db.executescript(
                "DELETE FROM cells; DELETE FROM merges; DELETE FROM records;"
            )
            count = _load_sheet(archive, member, db, epoch, options, normalize)
            for block in _sheet_blocks(db, title, count, options, helpers, cells):
                found = True
                yield block
        if not found:
            raise ParseFailed("Excel 没有表头")
