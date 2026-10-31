# -*- coding: utf-8 -*-
"""流式读取已发布 CSV/XLSX 产物，仅推断并保存工作表 schema。"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from math import isfinite
from uuid import NAMESPACE_URL
from uuid import uuid5

from model.document.model import Document
from model.structured_dataset import orm
from model.structured_dataset.model import SchemaColumn
from model.structured_dataset.model import StructuredDataset
from services.chunking.source import StoredParsedSource
from services.parsing.models import ParsedDocument

_MAX_SHEETS = 50
_MAX_COLUMNS = 256
_MAX_BLOCK_CHARS = 4 * 1024 * 1024


class StructuredSchemaService:
    """解析块 → 每张表的字段与计数 → 版本锁内持久化。"""

    def __init__(self, source=None):
        self.source = source or StoredParsedSource()

    def run(self, document: Document, version: int, md_key: str, json_key: str) -> bool:
        """返回 False 表示版本过期；产物错误由调用方处理。"""
        if document.parse_version > version:
            return False
        if document.file_type.lower() not in {"csv", "xlsx"}:
            raise ValueError("结构 schema 仅支持 CSV/XLSX")
        with self.source.read(document, version, md_key, json_key) as parsed:
            return orm.replace_current_version(
                document.id, version, _datasets(parsed, md_key, json_key)
            )


@dataclass
class _Column:
    kind: str = "null"
    non_null: int = 0

    def observe(self, value: object) -> None:
        kind = _value_type(value)
        if kind == "null":
            return
        self.non_null += 1
        if self.kind == "null":
            self.kind = kind
        elif self.kind != kind:
            if {self.kind, kind} <= {"integer", "number"}:
                self.kind = "number"
            else:
                self.kind = "mixed"


@dataclass
class _Sheet:
    columns: dict[str, _Column] = field(default_factory=dict)
    row_count: int = 0

    def add_column(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("解析字段名无效")
        if name not in self.columns:
            if len(self.columns) >= _MAX_COLUMNS:
                raise ValueError("工作表超过 256 列")
            self.columns[name] = _Column()

    def observe(self, row: object) -> None:
        if not isinstance(row, dict):
            raise ValueError("解析表格行必须是 JSON 对象")
        self.row_count += 1
        for name, value in row.items():
            self.add_column(name)
            self.columns[name].observe(value)

    def schema(self) -> list[SchemaColumn]:
        if not self.columns:
            raise ValueError("解析产物缺少表头，请重新解析文档")
        return [
            {
                "name": name,
                "type": column.kind,
                "nullable": column.non_null < self.row_count or not self.row_count,
            }
            for name, column in self.columns.items()
        ]


def _value_type(value: object) -> str:
    """仅相信 JSON 原始类型；日期已经丢失类型标记，不从字符串猜测。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("解析产物包含非有限数字")
        return "number"
    if isinstance(value, str):
        return "string"
    raise ValueError("解析单元格必须是 JSON 标量")


def _datasets(
    parsed: ParsedDocument, md_key: str, json_key: str
) -> Iterator[StructuredDataset]:
    """只保留每列计数和类型；一次解码一个有界块，不累计数据行。"""
    sheets: dict[str, _Sheet] = {}
    for block in parsed.blocks:
        if block.kind != "table":
            continue
        name = block.sheet if block.sheet is not None else parsed.title
        if not isinstance(name, str) or not name or len(name) > 255:
            raise ValueError("解析工作表名称无效")
        if name not in sheets:
            if len(sheets) >= _MAX_SHEETS:
                raise ValueError("文档超过 50 个工作表")
            sheets[name] = _Sheet()
        sheet = sheets[name]
        headers = block.metadata.get("headers", [])
        if not isinstance(headers, list):
            raise ValueError("解析表头必须是列表")
        for header in headers:
            sheet.add_column(header)
        if len(block.text) > _MAX_BLOCK_CHARS:
            raise ValueError("解析表格块超过 4Mi 字符")
        rows = json.loads(block.text)
        if not isinstance(rows, list):
            raise ValueError("解析表格必须是 JSON 数组")
        for row in rows:
            sheet.observe(row)
        del rows
    if not sheets:
        raise ValueError("解析产物没有工作表，请重新解析文档")
    for name, sheet in sheets.items():
        identity = json.dumps(
            [parsed.request.document_id, parsed.request.version, name],
            ensure_ascii=False,
        )
        yield StructuredDataset(
            id=str(uuid5(NAMESPACE_URL, f"rag:structured-dataset:{identity}")),
            document_id=parsed.request.document_id,
            version=parsed.request.version,
            sheet_name=name,
            file_type=parsed.request.file_type.lower(),
            schema_json=sheet.schema(),
            row_count=sheet.row_count,
            source_json_key=json_key,
            source_md_key=md_key,
        )
