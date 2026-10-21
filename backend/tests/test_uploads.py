# -*- coding: utf-8 -*-
"""大文件上传接口的联调测试。"""

import hashlib
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from model.document_task.model import DocumentTask
from services.common.exceptions import StorageError
from services.common.exceptions import UploadNotFound
from services.common.object_storage import client as storage_client


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
    repository = client.app.state.upload_repository
    session = repository.sessions[created["uploadId"]]

    from services.common.object_storage import client as storage_client

    storage_client().upload_part(
        session.object_key,
        session.s3_upload_id,
        1,
        content[:4],
    )

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
    assert client.app.state.dispatched_tasks == [body["taskId"]]

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
    assert client.app.state.dispatched_tasks == [first.json()["taskId"]]


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

def test_init_returns_existing_completed_document(client):
    content = b"0123456789"
    created = _init(client, content).json()
    repository = client.app.state.upload_repository
    session = repository.sessions[created["uploadId"]]
    session.status = "COMPLETED"
    session.completed_at = datetime.now(timezone.utc)
    client.app.state.document_repository.mark_exists(session.document_id)
    repository.add_task(
        DocumentTask(
            id="a" * 32,
            document_id=session.document_id,
            version=1,
            status="COMPLETED",
            stage="DONE",
            progress=100,
            step="处理完成",
        )
    )

    response = _init(client, content)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["documentId"] == created["documentId"]
    assert body["taskId"] == "a" * 32
    assert body["taskStatus"] == "COMPLETED"
    assert body["uploadedParts"] == []


def test_init_creates_new_session_after_expiry(client):
    content = b"0123456789"
    created = _init(client, content).json()
    repository = client.app.state.upload_repository
    old_session = repository.sessions[created["uploadId"]]
    old_session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    response = _init(client, content)

    assert response.status_code == 201
    body = response.json()
    assert body["uploadId"] != created["uploadId"]
    assert body["status"] == "UPLOADING"
    assert old_session.status == "ABORTING"
    assert old_session.abort_reason == "EXPIRED"

def test_complete_merge_timeout_detects_completed_object(client, monkeypatch):
    content = b"0123456789"
    created = _init(client, content).json()
    upload_id = created["uploadId"]
    for number, chunk in enumerate((content[:4], content[4:8], content[8:]), 1):
        _put(client, upload_id, number, chunk)

    repository = client.app.state.upload_repository
    session = repository.get(upload_id)
    storage = storage_client()
    storage.put(session.object_key, content, "text/plain")

    def timeout(*_args, **_kwargs):
        raise StorageError("timeout")

    monkeypatch.setattr(storage, "complete_multipart_upload", timeout)
    response = client.post(f"/api/uploads/{upload_id}/complete")

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert session.status == "COMPLETED"


def test_complete_merge_timeout_reverts_when_object_missing(client, monkeypatch):
    content = b"0123456789"
    created = _init(client, content).json()
    upload_id = created["uploadId"]
    for number, chunk in enumerate((content[:4], content[4:8], content[8:]), 1):
        _put(client, upload_id, number, chunk)

    repository = client.app.state.upload_repository
    session = repository.get(upload_id)
    storage = storage_client()
    original = storage.complete_multipart_upload

    def timeout(*_args, **_kwargs):
        raise StorageError("timeout")

    monkeypatch.setattr(storage, "complete_multipart_upload", timeout)
    response = client.post(f"/api/uploads/{upload_id}/complete")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert session.status == "UPLOADING"

    monkeypatch.setattr(storage, "complete_multipart_upload", original)
    retried = client.post(f"/api/uploads/{upload_id}/complete")
    assert retried.status_code == 200


def test_complete_merge_not_found_marks_failed(client, monkeypatch):
    content = b"0123456789"
    created = _init(client, content).json()
    upload_id = created["uploadId"]
    for number, chunk in enumerate((content[:4], content[4:8], content[8:]), 1):
        _put(client, upload_id, number, chunk)

    repository = client.app.state.upload_repository
    session = repository.get(upload_id)
    storage = storage_client()

    def missing(*_args, **_kwargs):
        raise UploadNotFound("gone")

    monkeypatch.setattr(storage, "complete_multipart_upload", missing)
    response = client.post(f"/api/uploads/{upload_id}/complete")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "UPLOAD_EXPIRED"
    assert session.status == "FAILED"

