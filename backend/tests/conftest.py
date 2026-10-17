# -*- coding: utf-8 -*-
"""API 测试夹具。"""

import pytest
from fastapi.testclient import TestClient

from config import Settings
from main import create_app
from task.runner import use_inline
from tests.memory import MemoryDocumentRepository
from tests.memory import open_memory_storage
from tests.memory import use_memory_records
from tests.memory import use_memory_tasks


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    settings = Settings(
        max_upload_bytes=1024,
        cors_origins=("http://localhost:5173",),
    )
    use_memory_records(monkeypatch, MemoryDocumentRepository())
    use_memory_tasks(monkeypatch)
    use_inline()
    application = create_app(settings, open_memory_storage)
    with TestClient(application) as test_client:
        yield test_client
