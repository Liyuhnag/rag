# -*- coding: utf-8 -*-
"""文档查询和删除。"""

from datetime import datetime
from datetime import timezone
from uuid import uuid4

from api.documents.validators import normalize_filename
from model.document import Document
from model.document import DocumentStatus


def _add_document(client, name="note.txt", content=b"hello"):
    now = datetime.now(timezone.utc)
    document_id = uuid4().hex
    document = Document(
        id=document_id,
        name=name,
        file_type=name.rsplit(".", 1)[-1],
        size_bytes=len(content),
        chunk_count=0,
        status=DocumentStatus.UPLOADED,
        uploaded_at=now,
        updated_at=now,
        object_key=f"documents/{document_id}/{name}",
        file_url="",
        previewable=False,
        preview_url=None,
    )
    client.app.state.document_repository.insert(document)
    return document


def test_normalize_filename_keeps_basename():
    assert normalize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert normalize_filename("手册.pdf") == "手册.pdf"


def test_list_returns_uploaded_document(client):
    _add_document(client, "note.txt", b"hello")

    response = client.get("/api/documents")

    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["totalItems"] == 1
    assert body["pagination"]["totalPages"] == 1
    assert body["data"][0]["name"] == "note.txt"
    assert body["data"][0]["status"] == "UPLOADED"


def test_get_and_delete_uploaded_document(client):
    created = _add_document(client, "note.txt", b"hello")

    fetched = client.get(f"/api/documents/{created.id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["name"] == "note.txt"
    assert body["sizeBytes"] == 5
    assert body["chunkCount"] == 0

    deleted = client.delete(f"/api/documents/{created.id}")
    assert deleted.status_code == 204

    missing = client.get(f"/api/documents/{created.id}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_post_documents_is_not_supported(client):
    response = client.post(
        "/api/documents",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 405


def test_get_missing_task(client):
    response = client.get("/api/tasks/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_get_missing_document(client):
    response = client.get("/api/documents/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_delete_missing_document(client):
    response = client.delete("/api/documents/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_list_rejects_invalid_page_size(client):
    response = client.get("/api/documents", params={"pageSize": 101})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
