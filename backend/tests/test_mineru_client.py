# -*- coding: utf-8 -*-
"""MinerU 客户端。"""

import io
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError

from services.parsing.mineru.client import UrllibTransport

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
        body: bytes | Iterable[bytes] | None,
        timeout: float,
    ) -> HttpResponse:
        content = body if isinstance(body, bytes) or body is None else b"".join(body)
        self.calls.append(_Call(method, url, headers, content, timeout))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def download(self, url, headers, target, timeout):
        response = self.send("GET", url, headers, None, timeout)
        target.write_bytes(response.body)
        return HttpResponse(response.status, b"")


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


def test_submit_streams_replayable_multipart_with_content_length(tmp_path, monkeypatch):
    path = tmp_path / "large.pdf"
    path.write_bytes(b"pdf" * 100_000)

    def forbidden_read_bytes(self):
        raise AssertionError("upload must not read the entire file")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    transport = _Transport(
        [
            HttpResponse(503, b""),
            HttpResponse(200, b'{"file_id":"f"}'),
            HttpResponse(200, b'{"job_id":"j"}'),
        ]
    )
    assert _client(transport).submit(path, "large.pdf", "sha") == MinerUJob("f", "j")
    first, second = transport.calls[:2]
    assert first.body == second.body
    assert int(first.headers["Content-Length"]) == len(first.body)
    assert b"pdf" * 100_000 in first.body


def test_download_retries_without_publishing_partial_file(tmp_path):
    class InterruptedTransport(_Transport):
        def download(self, url, headers, target, timeout):
            assert not (tmp_path / "job-1.zip").exists()
            assert not target.exists()
            self.calls.append(_Call("GET", url, headers, None, timeout))
            target.write_bytes(b"partial")
            if len(self.calls) == 1:
                raise OSError("interrupted")
            target.write_bytes(b"complete")
            return HttpResponse(200, b"")

    transport = InterruptedTransport([])
    path = _client(transport).download("job-1", tmp_path)
    assert path.read_bytes() == b"complete"
    assert list(tmp_path.iterdir()) == [path]


def test_download_failure_cleans_temporary_file_and_preserves_old_result(tmp_path):
    final = tmp_path / "job-1.zip"
    final.write_bytes(b"old")
    transport = _Transport([HttpResponse(404, b"missing")])
    with pytest.raises(MinerUJobLost):
        _client(transport).download("job-1", tmp_path)
    assert final.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [final]


def test_transport_download_reads_bounded_chunks(tmp_path, monkeypatch):
    class Response:
        status = 200
        headers = {}
        remaining = 200_000

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size=-1):
            assert 0 < size <= 64 * 1024
            count = min(size, self.remaining)
            self.remaining -= count
            return b"x" * count

    monkeypatch.setattr(
        "services.parsing.mineru.client.urlopen", lambda *a, **k: Response()
    )
    target = tmp_path / "result.zip"
    response = UrllibTransport().download("http://mineru/result", {}, target, 300)
    assert response == HttpResponse(200, b"")
    assert target.stat().st_size == 200_000


def test_transport_limits_json_response_size(monkeypatch):
    class Response:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024 + 1
            return b"x" * size

    monkeypatch.setattr(
        "services.parsing.mineru.client.urlopen", lambda *a, **k: Response()
    )
    with pytest.raises(MinerURequestError, match="响应.*过大"):
        UrllibTransport().send("GET", "http://mineru/status", {}, None, 60)


def test_transport_cleans_interrupted_download(tmp_path, monkeypatch):
    class Response:
        status = 200
        headers = {}
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("broken connection")

    monkeypatch.setattr(
        "services.parsing.mineru.client.urlopen", lambda *a, **k: Response()
    )
    target = tmp_path / "partial.zip"
    with pytest.raises(OSError):
        UrllibTransport().download("http://mineru/result", {}, target, 300)
    assert not target.exists()


def test_download_retryable_status_cleans_before_retry(tmp_path):
    transport = _Transport(
        [
            HttpResponse(503, b"not a zip"),
            HttpResponse(200, b"PK complete"),
        ]
    )
    path = _client(transport).download("job-1", tmp_path)
    assert path.read_bytes() == b"PK complete"
    assert len(transport.calls) == 2
    assert list(tmp_path.iterdir()) == [path]


def test_large_error_response_preserves_retryable_status(monkeypatch):
    def error_response(*args, **kwargs):
        raise HTTPError("http://mineru", 503, "busy", {}, io.BytesIO(b"x" * 2_000_000))

    monkeypatch.setattr("services.parsing.mineru.client.urlopen", error_response)
    response = UrllibTransport().send("GET", "http://mineru", {}, None, 60)
    assert response.status == 503
    assert len(response.body) <= 1024 * 1024


def test_transport_rejects_truncated_download(tmp_path, monkeypatch):
    class Response:
        status = 200
        headers = {"Content-Length": "100"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return b""

    monkeypatch.setattr(
        "services.parsing.mineru.client.urlopen", lambda *a, **k: Response()
    )
    target = tmp_path / "result.zip"
    with pytest.raises(OSError, match="不完整"):
        UrllibTransport().download("http://mineru/result", {}, target, 300)
    assert not target.exists()
