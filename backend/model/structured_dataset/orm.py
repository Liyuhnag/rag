# -*- coding: utf-8 -*-
"""structured_datasets 的 SQL；在文档版本锁内原子替换同版本 schema。"""

import json
from collections.abc import Iterable
from datetime import datetime
from datetime import timezone

from model.common.database import as_aware
from model.common.database import as_utc
from model.common.database import execute
from model.common.database import transaction
from model.structured_dataset.model import DatasetStatus
from model.structured_dataset.model import StructuredDataset


def _from_row(row: dict[str, object]) -> StructuredDataset:
    schema = row["schema_json"]
    if isinstance(schema, (str, bytes)):
        schema = json.loads(schema)
    return StructuredDataset(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        version=int(row["version"]),
        sheet_name=str(row["sheet_name"]),
        file_type=str(row["file_type"]),
        schema_json=schema,
        row_count=int(row["row_count"]),
        source_json_key=str(row["source_json_key"]),
        source_md_key=str(row["source_md_key"]),
        status=DatasetStatus(str(row["status"])),
        storage_table=None
        if row["storage_table"] is None
        else str(row["storage_table"]),
        created_at=as_aware(row["created_at"]),
        updated_at=as_aware(row["updated_at"]),
    )


def list_by_version(document_id: str, version: int) -> list[StructuredDataset]:
    """返回该版本的全部工作表 schema。"""
    rows = execute(
        "SELECT * FROM structured_datasets "
        "WHERE document_id = %(id)s AND version = %(version)s ORDER BY sheet_name, id",
        {"id": document_id, "version": version},
    )
    return [_from_row(row) for row in rows]


def replace_current_version(
    document_id: str,
    version: int,
    datasets: Iterable[StructuredDataset],
) -> bool:
    """先锁定版本再消费 schema 流；失败回滚全部删除和写入。"""
    now = datetime.now(timezone.utc)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT parse_version FROM documents WHERE id = %(id)s FOR UPDATE",
                {"id": document_id},
            )
            row = cursor.fetchone()
            if row is None or int(row["parse_version"]) != version:
                return False
            cursor.execute(
                "DELETE FROM structured_datasets WHERE document_id = %(id)s AND version = %(version)s",
                {"id": document_id, "version": version},
            )
            iterator = iter(datasets)
            try:
                for dataset in iterator:
                    if dataset.document_id != document_id or dataset.version != version:
                        raise ValueError("数据集的文档或版本不匹配")
                    if (
                        dataset.status != DatasetStatus.SCHEMA_READY
                        or dataset.storage_table is not None
                    ):
                        raise ValueError("schema 元数据不能标记为已导入数据表")
                    _insert(cursor, dataset, now)
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
    return True


def _insert(cursor, dataset: StructuredDataset, now: datetime) -> None:
    cursor.execute(
        """
        INSERT INTO structured_datasets (
            id, document_id, version, sheet_name, file_type, schema_json,
            row_count, status, storage_table, source_json_key, source_md_key,
            created_at, updated_at
        ) VALUES (
            %(id)s, %(document_id)s, %(version)s, %(sheet_name)s, %(file_type)s,
            %(schema_json)s, %(row_count)s, %(status)s, %(storage_table)s,
            %(source_json_key)s, %(source_md_key)s, %(created_at)s, %(updated_at)s
        )
        """,
        {
            "id": dataset.id,
            "document_id": dataset.document_id,
            "version": dataset.version,
            "sheet_name": dataset.sheet_name,
            "file_type": dataset.file_type,
            "schema_json": json.dumps(
                dataset.schema_json, ensure_ascii=False, allow_nan=False
            ),
            "row_count": dataset.row_count,
            "status": dataset.status.value,
            "storage_table": dataset.storage_table,
            "source_json_key": dataset.source_json_key,
            "source_md_key": dataset.source_md_key,
            "created_at": as_utc(dataset.created_at or now),
            "updated_at": as_utc(dataset.updated_at or now),
        },
    )
