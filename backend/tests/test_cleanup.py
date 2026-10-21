# -*- coding: utf-8 -*-
"""上传清理任务。"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from services.common.object_storage import client as storage_client
from services.upload.jobs import cleanup


def _created(client) -> tuple[dict, object]:
    content = b"0123456789"
    response = client.post(
        "/api/uploads/init",
        json={
            "filename": "demo.txt",
            "sizeBytes": len(content),
            "fingerprint": "a" * 64,
        },
    )
    assert response.status_code == 201
    body = response.json()
    repository = client.app.state.upload_repository
    session = repository.get(body["uploadId"])
    return body, session


def test_expire_upload_aborts_mpu(client):
    body, session = _created(client)
    now = datetime.now(timezone.utc)
    session.expires_at = now - timedelta(seconds=1)
    storage = storage_client()

    count = cleanup.expire_uploads(now, 10)

    assert count == 1
    assert session.status == "EXPIRED"
    assert session.s3_upload_id not in storage.multipart


def test_retry_aborting_finishes_session(client):
    body, session = _created(client)
    now = datetime.now(timezone.utc)
    session.status = "ABORTING"
    session.abort_reason = "USER"
    session.updated_at = now - timedelta(minutes=10)
    storage = storage_client()

    count = cleanup.retry_aborting(
        now,
        now - timedelta(minutes=5),
        10,
    )

    assert count == 1
    assert session.status == "ABORTED"
    assert session.s3_upload_id not in storage.multipart


def test_cleanup_orphan_mpu(client):
    storage = storage_client()
    upload_id = storage.create_multipart_upload(
        "documents/orphan/source/demo.txt",
        "text/plain",
    )
    upload = storage.multipart[upload_id]
    upload["initiated"] = datetime.now(timezone.utc) - timedelta(hours=25)

    count = cleanup.cleanup_orphan_mpu(
        datetime.now(timezone.utc),
        24,
        10,
    )

    assert count == 1
    assert upload_id not in storage.multipart


def test_cleanup_orphan_object(client):
    body, session = _created(client)
    now = datetime.now(timezone.utc)
    session.status = "FAILED"
    session.updated_at = now - timedelta(days=8)
    storage = storage_client()
    storage.put(session.object_key, b"0123456789", "text/plain")

    count = cleanup.cleanup_orphan_object(now, 7, 10)

    assert count == 1
    assert session.object_key not in storage.objects


def test_cleanup_old_sessions(client):
    body, session = _created(client)
    now = datetime.now(timezone.utc)
    session.status = "COMPLETED"
    session.updated_at = now - timedelta(days=31)
    repository = client.app.state.upload_repository

    count = cleanup.cleanup_old_sessions(now, 30, 10)

    assert count == 1
    assert repository.get(body["uploadId"]) is None


def test_recover_completing_merges_and_completes(client):
    content = b"0123456789"
    created = client.post(
        "/api/uploads/init",
        json={
            "filename": "demo.txt",
            "sizeBytes": len(content),
            "fingerprint": "b" * 64,
        },
    ).json()
    upload_id = created["uploadId"]
    for number, chunk in enumerate((content[:4], content[4:8], content[8:]), 1):
        response = client.put(
            f"/api/uploads/{upload_id}/parts/{number}",
            content=chunk,
            headers={"Content-Length": str(len(chunk))},
        )
        assert response.status_code == 200

    repository = client.app.state.upload_repository
    session = repository.get(upload_id)
    now = datetime.now(timezone.utc)
    assert repository.claim_completing(upload_id, now) is True
    session.updated_at = now - timedelta(minutes=10)

    count = cleanup.recover_completing_uploads(
        now,
        now - timedelta(minutes=5),
        10,
        300,
    )

    assert count == 1
    assert session.status == "COMPLETED"
    assert repository.find_task(session.document_id, 1) is not None


def test_recover_completing_expired_aborts(client):
    body, session = _created(client)
    now = datetime.now(timezone.utc)
    repository = client.app.state.upload_repository
    assert repository.claim_completing(body["uploadId"], now) is True
    session.expires_at = now - timedelta(seconds=1)
    session.updated_at = now - timedelta(minutes=10)

    cleanup.recover_completing_uploads(
        now,
        now - timedelta(minutes=5),
        10,
        300,
    )

    assert session.status == "EXPIRED"
    assert session.s3_upload_id not in storage_client().multipart
