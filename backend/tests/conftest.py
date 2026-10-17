# -*- coding: utf-8 -*-
"""API 测试夹具。"""

import pytest
from fastapi.testclient import TestClient

from config import Settings
from main import create_app


@pytest.fixture
def upload_dir(tmp_path):
    return tmp_path / "uploads"


@pytest.fixture
def client(upload_dir):
    settings = Settings(
        max_upload_bytes=1024,
        upload_dir=upload_dir,
        cors_origins=("http://localhost:5173",),
    )
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client
