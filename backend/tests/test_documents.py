# -*- coding: utf-8 -*-
"""文档接口。上传写入存储，检索和生成仍未接入。"""

from api.documents.validators import normalize_filename


def _upload(client, name, content):
    return client.post(
        "/api/documents",
        files={"file": (name, content, "application/octet-stream")},
    )


def test_normalize_filename_keeps_basename():
    assert normalize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert normalize_filename("手册.pdf") == "手册.pdf"


def test_upload_stores_document(client):
    content = b"%PDF-1.4 sample"
    response = _upload(client, "手册.pdf", content)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "手册.pdf"
    assert body["type"] == "pdf"
    assert body["sizeBytes"] == len(content)
    assert body["chunkCount"] == 0
    assert body["status"] == "UPLOADED"
    assert body["id"]
    assert body["uploadedAt"]


def test_list_returns_uploaded_document(client):
    _upload(client, "note.txt", b"hello")

    response = client.get("/api/documents")

    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["totalItems"] == 1
    assert body["pagination"]["totalPages"] == 1
    assert body["data"][0]["name"] == "note.txt"


def test_get_and_delete_uploaded_document(client):
    created = _upload(client, "note.txt", b"hello").json()
    document_id = created["id"]

    fetched = client.get(f"/api/documents/{document_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "note.txt"

    deleted = client.delete(f"/api/documents/{document_id}")
    assert deleted.status_code == 204

    missing = client.get(f"/api/documents/{document_id}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_upload_rejects_unsupported_type(client):
    response = _upload(client, "virus.exe", b"MZ")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    assert "allowed" in body["error"]["details"]


def test_upload_rejects_empty_file(client):
    response = _upload(client, "empty.txt", b"")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_FILE"


def test_upload_rejects_oversized_file(client):
    response = _upload(client, "big.txt", b"a" * 1025)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_upload_requires_file(client):
    response = client.post("/api/documents")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "traceback" not in response.text.lower()


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
