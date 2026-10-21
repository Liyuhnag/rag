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
from tests.memory import use_open_queue
from tests.memory import use_memory_records
from tests.memory import use_memory_tasks
from tests.memory import use_memory_upload_repository


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    settings = Settings(
        max_upload_bytes=1024,
        max_document_bytes=100,
        upload_chunk_bytes=4,
        cors_origins=("http://localhost:5173",),
    )
    upload_repository = MemoryUploadRepository()
    use_memory_upload_repository(monkeypatch, upload_repository)
    document_repository = MemoryDocumentRepository()
    use_memory_records(monkeypatch, document_repository)
    use_memory_tasks(monkeypatch)
    use_inline_tasks()
    use_open_queue(monkeypatch)
    application = create_app(settings, open_memory_storage)
    application.state.upload_repository = upload_repository
    application.state.document_repository = document_repository
    with TestClient(application) as test_client:
        yield test_client
