# -*- coding: utf-8 -*-
"""L3 三级存储：从 MySQL document_chunks 与 documents 批量联表回源父块。"""

from collections.abc import Callable
from collections.abc import Sequence

from model.document_chunk import orm as chunk_orm
from services.retrieval.fusion.backfill.models import ParentChunkPayload


class MySQLParentStore:
    """L3：按 parent_id 列表从 MySQL 批量查询 READY 文档最新版本的父块。"""

    def __init__(
        self,
        db_loader: Callable[[Sequence[str]], list[dict[str, object]]] | None = None,
    ) -> None:
        self._load_rows = db_loader or chunk_orm.list_parents_by_ids

    def get_many(self, parent_ids: Sequence[str]) -> dict[str, ParentChunkPayload]:
        """从 MySQL 批量查询父块并转换为 ParentChunkPayload 字典。"""
        if not parent_ids:
            return {}
        rows = self._load_rows(parent_ids)
        results: dict[str, ParentChunkPayload] = {}
        for row in rows:
            payload = _row_to_payload(row)
            results[payload.parent_id] = payload
        return results


def _row_to_payload(row: dict[str, object]) -> ParentChunkPayload:
    page_start = row.get("page_start")
    page_end = row.get("page_end")
    return ParentChunkPayload(
        parent_id=str(row["id"]),
        document_id=str(row["document_id"]),
        document_name=str(row.get("document_name", "")),
        version=int(row.get("version", 1)),
        parent_index=int(row.get("parent_index") or 0),
        file_type=str(row.get("file_type", "")),
        content=str(row.get("content", "")),
        heading_path=str(row.get("heading_path", "")),
        locator=str(row.get("locator", "")),
        page_start=None if page_start is None else int(page_start),
        page_end=None if page_end is None else int(page_end),
        token_count=int(row.get("token_count") or 0),
    )
