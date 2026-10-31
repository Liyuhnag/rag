# -*- coding: utf-8 -*-
"""API 测试夹具。"""

import pytest
from fastapi.testclient import TestClient

from config import Settings
from main import create_app
from tests.memory import MemoryDocumentRepository
from tests.memory import MemoryUploadRepository
from tests.memory import open_memory_storage
from tests.memory import use_inline_tasks
from tests.memory import use_memory_records
from tests.memory import use_memory_upload_repository
from services.upload.steps import completion as upload_completion


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    settings = Settings(
        max_document_bytes=100,
        upload_chunk_bytes=4,
        cors_origins=("http://localhost:5173",),
    )
    upload_repository = MemoryUploadRepository()
    use_memory_upload_repository(monkeypatch, upload_repository)
    document_repository = MemoryDocumentRepository()
    use_memory_records(monkeypatch, document_repository)
    use_inline_tasks()
    dispatched_tasks: list[str] = []
    monkeypatch.setattr(
        upload_completion,
        "dispatch_task",
        lambda task_id, stage: dispatched_tasks.append(task_id) or True,
    )
    application = create_app(settings, open_memory_storage)
    application.state.upload_repository = upload_repository
    application.state.document_repository = document_repository
    application.state.dispatched_tasks = dispatched_tasks
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def parse_lease(monkeypatch):
    """编排单测只替换 SQL 边界；租约事务和心跳另有专门测试。"""
    from contextlib import nullcontext

    from model.document_task import orm as task_orm

    monkeypatch.setattr(task_orm, "renew", lambda *args: True)
    monkeypatch.setattr(task_orm, "owns_lease", lambda *args: True)
    monkeypatch.setattr(task_orm, "lease_guard", lambda *args: nullcontext())
