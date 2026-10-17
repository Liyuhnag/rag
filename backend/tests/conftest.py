# -*- coding: utf-8 -*-
"""API 测试夹具。"""

import pytest
from fastapi.testclient import TestClient

from config import Settings
from main import create_app
from tests.memory import memory_document_store


@pytest.fixture
def client():
    settings = Settings(
        max_upload_bytes=1024,
        cors_origins=("http://localhost:5173",),
    )
    application = create_app(settings, memory_document_store(settings))
    with TestClient(application) as test_client:
        yield test_client
