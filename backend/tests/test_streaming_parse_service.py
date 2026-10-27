# -*- coding: utf-8 -*-
"""生产编排必须走块迭代器，不能退回完整对象读取。"""

from datetime import datetime
from datetime import timezone

import pytest

from model.document.model import Document
from model.document.model import DocumentStatus
from services.parsing.service import DocumentParsingService


class Store:
    def __init__(self, content):
        self.content = content
        self.objects = {}

    def read_object(self, key):
        raise AssertionError("production must not read whole objects")

    def download_object(self, key, path):
        path.write_bytes(self.content)

    def upload_file(self, key, path, content_type):
        self.objects[key] = path.read_bytes()

    def write_object(self, key, content, content_type):
        self.objects[key] = content


@pytest.mark.parametrize(
    "file_type,content,expected",
    [
        ("txt", "正文".encode(), "# demo\n\n正文\n"),
        ("md", "# 标题\r\n正文  ".encode(), "# 标题\r\n正文  "),
        ("csv", b"name,score\na,2\n", '[{"name": "a", "score": 2}]\n'),
    ],
)
def test_service_consumes_streaming_parser(file_type, content, expected):
    now = datetime.now(timezone.utc)
    document = Document(
        id="doc",
        name=f"demo.{file_type}",
        file_type=file_type,
        size_bytes=len(content),
        chunk_count=0,
        status=DocumentStatus.UPLOADED,
        uploaded_at=now,
        updated_at=now,
        object_key="original",
        file_url="",
        previewable=False,
        preview_url=None,
    )
    store = Store(content)
    service = DocumentParsingService(store)
    outcome = service.parse(document, 1)
    stored = service.upload(outcome.parsed)
    assert store.objects[stored.md_key].decode() == expected
