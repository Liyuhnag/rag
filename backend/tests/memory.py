# -*- coding: utf-8 -*-
"""测试用的进程内文档存储。"""

import pytest

from config import Settings
from model.document import Document
from model.document import orm as document_orm
from services.document import configure
from services.document.object_storage import ObjectStorage


class MemoryObjectStorage(ObjectStorage):
    """进程内对象存储。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, content: bytes, content_type: str) -> None:
        del content_type
        self.objects[key] = content

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class MemoryDocumentRepository:
    """进程内文档记录。"""

    def __init__(self) -> None:
        self.records: dict[str, Document] = {}

    def insert(self, document: Document) -> None:
        self.records[document.id] = document

    def get(self, document_id: str) -> Document | None:
        return self.records.get(document_id)

    def delete(self, document_id: str) -> Document | None:
        return self.records.pop(document_id, None)

    def list_page(
        self,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[Document], int]:
        records = list(self.records.values())
        reverse = sort_order != "asc"

        def sort_key(record: Document) -> tuple[object, str]:
            if sort_by == "name":
                value: object = record.name
            elif sort_by == "size_bytes":
                value = record.size_bytes
            else:
                value = record.uploaded_at
            return (value, record.id)

        records.sort(key=sort_key, reverse=reverse)
        start = (page - 1) * page_size
        return records[start : start + page_size], len(records)


def use_memory_records(
    monkeypatch: pytest.MonkeyPatch,
    records: MemoryDocumentRepository,
) -> None:
    """让文档服务的数据库调用走进程内记录。"""
    monkeypatch.setattr(document_orm, "insert", records.insert)
    monkeypatch.setattr(document_orm, "get", records.get)
    monkeypatch.setattr(document_orm, "delete", records.delete)
    monkeypatch.setattr(document_orm, "list_page", records.list_page)


def open_memory_storage(settings: Settings) -> None:
    """接入进程内对象存储。"""
    configure(
        MemoryObjectStorage(),
        bucket=settings.s3_bucket,
        file_base_url=settings.file_base_url,
    )
