# -*- coding: utf-8 -*-
"""MinerU 客户端。"""

from dataclasses import dataclass

import pytest

from services.parsing.mineru import HttpResponse
from services.parsing.mineru import MinerUClient
from services.parsing.mineru import MinerUJob
from services.parsing.mineru import MinerUStatus
from services.parsing.errors import MinerUJobLost
from services.parsing.errors import MinerURequestError
from services.parsing.errors import ParserUnavailable


@dataclass
class _Call:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout: float


class _Transport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[_Call] = []

    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        self.calls.append(_Call(method, url, headers, body, timeout))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(transport: _Transport, **kwargs: object) -> MinerUClient:
    options = {
        "base_url": "http://mineru.internal/",
        "api_key": "secret-key",
        "transport": transport,
        "sleep": lambda _seconds: None,
    }
    options.update(kwargs)
    return MinerUClient(**options)  # type: ignore[arg-type]


def test_submit_uploads_file_then_creates_job(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF")
    transport = _Transport(
        [
            HttpResponse(200, b'{"file_id": "file-1"}'),
            HttpResponse(200, b'{"job_id": "job-1"}'),
        ]
    )

    job = _client(transport).submit(path, "nested/a.pdf", "abc123")

    assert job == MinerUJob(file_id="file-1", job_id="job-1")
    assert transport.calls[0].url == "http://mineru.internal/v1/files"
    assert transport.calls[0].timeout == 300
    assert b'filename="a.pdf"' in (transport.calls[0].body or b"")
    assert b"%PDF" in (transport.calls[0].body or b"")
    assert b"abc123" in (transport.calls[0].body or b"")
    assert transport.calls[1].url == "http://mineru.internal/v1/jobs"
    assert transport.calls[1].body == b'{"file_id": "file-1"}'
    assert transport.calls[0].headers["Authorization"] == "Bearer secret-key"


def test_retries_retryable_status_then_succeeds(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"pdf")
    slept: list[float] = []
    transport = _Transport(
        [
            HttpResponse(429, b""),
            HttpResponse(200, b'{"file_id":"f"}'),
            HttpResponse(503, b""),
            HttpResponse(200, b'{"job_id":"j"}'),
        ]
    )
    client = _client(transport, sleep=slept.append)

    assert client.submit(path, "a.pdf", "digest") == MinerUJob("f", "j")
    assert slept == [1.0, 1.0]


def test_client_error_is_not_retried(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"pdf")
    transport = _Transport([HttpResponse(400, b"no")])

    with pytest.raises(MinerURequestError, match="400"):
        _client(transport).submit(path, "a.pdf", "digest")

    assert len(transport.calls) == 1


def test_missing_job_is_not_retried():
    transport = _Transport([HttpResponse(404, b"")])

    with pytest.raises(MinerUJobLost):
        _client(transport).status("job-1")

    assert len(transport.calls) == 1


def test_unavailable_after_retries():
    transport = _Transport([HttpResponse(500, b"")] * 3)

    with pytest.raises(ParserUnavailable, match="500"):
        _client(transport).status("job-1")

    assert len(transport.calls) == 3


def test_connection_errors_are_retried():
    transport = _Transport(
        [
            OSError("down"),
            OSError("down"),
            HttpResponse(200, b'{"status":"running"}'),
        ]
    )

    assert _client(transport).status("job-1") == MinerUStatus("running")
    assert len(transport.calls) == 3


def test_download_writes_zip(tmp_path):
    transport = _Transport([HttpResponse(200, b"PK zip")])

    path = _client(transport).download("job-1", tmp_path)

    assert path.read_bytes() == b"PK zip"
    assert path.name == "job-1.zip"
    assert transport.calls[0].timeout == 300


def test_invalid_job_id_is_rejected_before_request():
    transport = _Transport([])

    with pytest.raises(MinerURequestError):
        _client(transport).status("../job")

    assert transport.calls == []


def test_missing_base_url_is_rejected():
    transport = _Transport([])
    client = _client(transport, base_url="", api_key="")

    with pytest.raises(ParserUnavailable, match="MINERU_API_URL"):
        client.status("job-1")

    assert transport.calls == []


def test_api_key_is_not_included_in_errors(caplog, tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"pdf")
    transport = _Transport([HttpResponse(401, b"secret-key")])

    with pytest.raises(MinerURequestError) as caught:
        _client(transport).submit(path, "a.pdf", "digest")

    assert "secret-key" not in str(caught.value)
    assert "secret-key" not in caplog.text


def test_empty_api_key_omits_authorization():
    transport = _Transport([HttpResponse(200, b'{"status":"queued"}')])

    status = _client(transport, api_key="").status("job-1")

    assert status == MinerUStatus("queued")
    assert "Authorization" not in transport.calls[0].headers
