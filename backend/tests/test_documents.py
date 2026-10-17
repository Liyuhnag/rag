# -*- coding: utf-8 -*-
"""文档接口。服务尚未实现，合法请求在校验后返回 501。"""

from api.uploads import normalize_filename


def _upload(client, name, content, operator=None):
    data = None
    if operator is not None:
        data = {"operator": operator}
    return client.post(
        "/api/documents",
        files={"file": (name, content, "application/octet-stream")},
        data=data,
    )


def test_normalize_filename_keeps_basename():
    assert normalize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert normalize_filename("手册.pdf") == "手册.pdf"


def test_list_documents_is_not_ready(client):
    response = client.get("/api/documents")

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"


def test_upload_is_not_ready_after_validation(client):
    response = _upload(client, "手册.pdf", b"%PDF-1.4 sample", operator="张三")

    assert response.status_code == 501
    assert response.json()["error"]["message"] == "文档服务尚未接入"


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


def test_get_document_is_not_ready(client):
    response = client.get("/api/documents/missing")

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"


def test_delete_document_is_not_ready(client):
    response = client.delete("/api/documents/missing")

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"


def test_list_rejects_invalid_page_size(client):
    response = client.get("/api/documents", params={"pageSize": 101})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
