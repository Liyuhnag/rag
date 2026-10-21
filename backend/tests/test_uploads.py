# -*- coding: utf-8 -*-
"""大文件上传接口的联调测试。"""

import hashlib


def _fingerprint(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _init(client, content: bytes, **overrides):
    payload = {
        "filename": "sample.txt",
        "sizeBytes": len(content),
        "fingerprint": _fingerprint(content),
    }
    payload.update(overrides)
    return client.post("/api/uploads/init", json=payload)


def _put(client, upload_id: str, part_number: int, content: bytes):
    return client.put(
        f"/api/uploads/{upload_id}/parts/{part_number}",
        content=content,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_init_creates_session(client):
    content = b"0123456789"
    response = _init(client, content)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "UPLOADING"
    assert body["chunkSize"] == 4
    assert body["totalParts"] == 3
    assert body["uploadedParts"] == []
    assert body["documentId"]
    assert body["uploadId"]
    assert body["expiresAt"]


def test_init_reuses_active_session(client):
    content = b"0123456789"
    created = _init(client, content).json()
    uploaded = _put(client, created["uploadId"], 1, content[:4])
    assert uploaded.status_code == 200

    reused = _init(client, content)

    assert reused.status_code == 200
    body = reused.json()
    assert body["uploadId"] == created["uploadId"]
    assert body["documentId"] == created["documentId"]
    assert body["uploadedParts"] == [{"partNumber": 1, "sizeBytes": 4}]


def test_init_conflicts_when_filename_changes(client):
    content = b"0123456789"
    _init(client, content)

    response = _init(client, content, filename="other.txt")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FINGERPRINT_CONFLICT"


def test_upload_complete_and_query_task(client):
    content = b"0123456789"
    created = _init(client, content).json()
    upload_id = created["uploadId"]

    assert _put(client, upload_id, 1, content[:4]).status_code == 200
    assert _put(client, upload_id, 2, content[4:8]).status_code == 200
    assert _put(client, upload_id, 3, content[8:]).status_code == 200

    status = client.get(f"/api/uploads/{upload_id}")
    assert status.status_code == 200
    assert len(status.json()["uploadedParts"]) == 3

    completed = client.post(f"/api/uploads/{upload_id}/complete")
    assert completed.status_code == 200
    body = completed.json()
    assert body["status"] == "COMPLETED"
    assert body["taskStatus"] == "PENDING"
    assert body["taskId"]

    after = client.get(f"/api/uploads/{upload_id}")
    assert after.status_code == 200
    assert after.json()["status"] == "COMPLETED"
    assert after.json()["uploadedParts"] == []

    task = client.get(f"/api/tasks/{body['taskId']}")
    assert task.status_code == 200
    task_body = task.json()
    assert task_body["id"] == body["taskId"]
    assert task_body["documentId"] == created["documentId"]
    assert task_body["status"] == "PENDING"
    assert task_body["stage"] == "PENDING"
    assert task_body["progress"] == 0
    assert task_body["step"] == "等待处理"
    assert task_body["version"] == 1


def test_complete_is_idempotent(client):
    content = b"0123456789"
    created = _init(client, content).json()
    upload_id = created["uploadId"]
    for number, chunk in enumerate((content[:4], content[4:8], content[8:]), 1):
        _put(client, upload_id, number, chunk)

    first = client.post(f"/api/uploads/{upload_id}/complete")
    second = client.post(f"/api/uploads/{upload_id}/complete")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["taskId"] == first.json()["taskId"]
    assert second.json()["documentId"] == first.json()["documentId"]


def test_complete_reports_missing_parts(client):
    content = b"0123456789"
    created = _init(client, content).json()
    upload_id = created["uploadId"]
    _put(client, upload_id, 1, content[:4])

    response = client.post(f"/api/uploads/{upload_id}/complete")

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "UPLOAD_INCOMPLETE"
    assert body["error"]["details"]["missingParts"] == [2, 3]


def test_part_size_and_number_validation(client):
    content = b"0123456789"
    created = _init(client, content).json()
    upload_id = created["uploadId"]

    wrong_size = _put(client, upload_id, 1, content[:3])
    assert wrong_size.status_code == 422
    assert wrong_size.json()["error"]["code"] == "INVALID_PART_SIZE"

    wrong_number = _put(client, upload_id, 4, content[:4])
    assert wrong_number.status_code == 422
    assert wrong_number.json()["error"]["code"] == "INVALID_PART_NUMBER"


def test_cancel_upload(client):
    content = b"0123456789"
    created = _init(client, content).json()
    upload_id = created["uploadId"]

    deleted = client.delete(f"/api/uploads/{upload_id}")
    assert deleted.status_code == 204

    status = client.get(f"/api/uploads/{upload_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "ABORTED"

    completed = client.post(f"/api/uploads/{upload_id}/complete")
    assert completed.status_code == 410
    assert completed.json()["error"]["code"] == "UPLOAD_ABORTED"


def test_delete_completed_upload_conflicts(client):
    content = b"0123456789"
    created = _init(client, content).json()
    upload_id = created["uploadId"]
    for number, chunk in enumerate((content[:4], content[4:8], content[8:]), 1):
        _put(client, upload_id, number, chunk)
    assert client.post(f"/api/uploads/{upload_id}/complete").status_code == 200

    response = client.delete(f"/api/uploads/{upload_id}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "UPLOAD_ALREADY_COMPLETED"


def test_init_validation(client):
    content = b"0123456789"

    invalid_name = _init(client, content, filename="")
    assert invalid_name.status_code == 400
    assert invalid_name.json()["error"]["code"] == "INVALID_FILENAME"

    invalid_fp = _init(client, content, fingerprint="abc")
    assert invalid_fp.status_code == 400
    assert invalid_fp.json()["error"]["code"] == "INVALID_FINGERPRINT"

    too_large = _init(client, content, sizeBytes=101)
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "FILE_TOO_LARGE"

    bad_type = _init(client, content, filename="payload.exe")
    assert bad_type.status_code == 415
    assert bad_type.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_get_missing_upload(client):
    response = client.get("/api/uploads/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UPLOAD_NOT_FOUND"


def test_cors_allows_put(client):
    response = client.options(
        "/api/uploads/init",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PUT",
        },
    )

    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
