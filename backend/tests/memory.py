# -*- coding: utf-8 -*-
"""测试用的进程内文档存储。"""

from dataclasses import replace
from datetime import datetime
from datetime import timezone

import pytest

from config import Settings
from model.document import Document
from model.document import orm as document_orm
from services.common.object_storage import ObjectStorage
from services.common.object_storage import configure
from model.task import ProcessingTask
from model.task import orm as task_orm


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


class MemoryTaskRepository:
    """进程内异步任务。"""

    def __init__(self) -> None:
        self.tasks: dict[str, ProcessingTask] = {}

    def insert(self, task: ProcessingTask) -> None:
        self.tasks[task.id] = task

    def get(self, task_id: str) -> ProcessingTask | None:
        return self.tasks.get(task_id)

    def set_status(self, task_id: str, status: str) -> None:
        task = self.tasks[task_id]
        self.tasks[task_id] = replace(
            task,
            status=status,
            updated_at=datetime.now(timezone.utc),
        )

    def finish(self, task_id: str, document_id: str) -> None:
        task = self.tasks[task_id]
        self.tasks[task_id] = replace(
            task,
            status="STORED",
            document_id=document_id,
            updated_at=datetime.now(timezone.utc),
        )

    def fail(self, task_id: str, message: str) -> None:
        task = self.tasks[task_id]
        self.tasks[task_id] = replace(
            task,
            status="FAILED",
            error_message=message[:1024],
            updated_at=datetime.now(timezone.utc),
        )


def use_memory_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """让异步任务记录留在进程内。"""
    tasks = MemoryTaskRepository()
    monkeypatch.setattr(task_orm, "insert", tasks.insert)
    monkeypatch.setattr(task_orm, "get", tasks.get)
    monkeypatch.setattr(task_orm, "set_status", tasks.set_status)
    monkeypatch.setattr(task_orm, "finish", tasks.finish)
    monkeypatch.setattr(task_orm, "fail", tasks.fail)


def open_memory_storage(settings: Settings) -> None:
    """接入进程内对象存储。"""
    configure(
        MemoryObjectStorage(),
        bucket=settings.s3_bucket,
        file_base_url=settings.file_base_url,
    )
