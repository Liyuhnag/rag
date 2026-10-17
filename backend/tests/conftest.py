# -*- coding: utf-8 -*-
"""API 测试夹具。"""

import os

import pytest
from fastapi.testclient import TestClient

os.environ["STORAGE_BACKEND"] = "memory"

from config import Settings
from main import create_app


@pytest.fixture
def client():
    settings = Settings(
        max_upload_bytes=1024,
        cors_origins=("http://localhost:5173",),
        storage_backend="memory",
    )
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client
