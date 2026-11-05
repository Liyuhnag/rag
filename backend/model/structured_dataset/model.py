# -*- coding: utf-8 -*-
"""每个文档版本、工作表对应一条 schema 记录，不代表可查询的数据表。"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TypedDict


class SchemaColumn(TypedDict):
    """从解析后的 JSON 值推断列类型。"""

    name: str
    type: str
    nullable: bool


class DatasetStatus(str, Enum):
    """只有 schema 就绪；尚未导入数据行。"""

    SCHEMA_READY = "SCHEMA_READY"


@dataclass
class StructuredDataset:
    """structured_datasets 表的一行。"""

    id: str
    document_id: str
    version: int
    sheet_name: str
    file_type: str
    schema_json: list[SchemaColumn]
    row_count: int
    source_json_key: str
    source_md_key: str
    status: DatasetStatus = DatasetStatus.SCHEMA_READY
    storage_table: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
