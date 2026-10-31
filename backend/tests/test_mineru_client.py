# -*- coding: utf-8 -*-
"""MinerU 客户端。"""

import io
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import AbstractHTTPHandler
from urllib.request import HTTPRedirectHandler
from urllib.request import build_opener

import pytest

from services.parsing.mineru.client import UrllibTransport

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


def _response(data=None, code=0):
    return HttpResponse(200, json.dumps({"code": code, "data": data}).encode())


def _batch(state="done", **extra):
    return _response(
        {
            "batch_id": "job-1",
            "extract_result": [{"state": state, "data_id": "digest", **extra}],
        }
    )


def _upload_response():
    return _response(
        {
            "batch_id": "job-1",
            "file_urls": ["https://storage.example/upload?signature=private"],
        }
    )


def test_submit_uses_official_one_file_batch_and_signed_put(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF")
    transport = _Transport([_upload_response(), HttpResponse(200, b"")])

    job = _client(transport).submit(path, "nested/a.pdf", "digest")

    assert job == MinerUJob(file_id="digest", job_id="job-1")
    control, upload = transport.calls
    assert control.url == "http://mineru.internal/api/v4/file-urls/batch"
    assert json.loads(control.body) == {
        "files": [{"name": "a.pdf", "data_id": "digest"}],
        "model_version": "vlm",
    }
    assert control.headers["Authorization"] == "Bearer secret-key"
    assert control.headers["Content-Type"] == "application/json"
    assert upload.method == "PUT"
    assert upload.url == "https://storage.example/upload?signature=private"
    assert upload.body == b"%PDF"
    assert upload.headers == {"Content-Length": "4"}
    assert upload.timeout == 300


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("waiting-file", "queued"),
        ("pending", "queued"),
        ("running", "running"),
        ("converting", "running"),
        ("done", "completed"),
        ("failed", "failed"),
    ],
)
def test_status_normalizes_official_states(remote, expected):
    transport = _Transport(
        [_batch(remote, err_msg="failure" if remote == "failed" else "")]
    )
    result = _client(transport).status("job-1")
    assert result == MinerUStatus(expected, "failure" if remote == "failed" else None)
    assert transport.calls[0].url.endswith("/api/v4/extract-results/batch/job-1")


@pytest.mark.parametrize(
    "code,error",
    [
        (-60012, MinerUJobLost),
        ("A0202", MinerURequestError),
        ("A0211", MinerURequestError),
        (-60013, MinerURequestError),
        (-60005, MinerURequestError),
        (-10001, ParserUnavailable),
        (-60001, ParserUnavailable),
        (-60007, ParserUnavailable),
        (-60009, ParserUnavailable),
    ],
)
def test_business_errors_are_classified_without_exposing_response(code, error, caplog):
    response = HttpResponse(
        200, json.dumps({"code": code, "msg": "secret-key"}).encode()
    )
    transport = _Transport([response] * 3)
    with pytest.raises(error) as caught:
        _client(transport).status("job-1")
    assert "secret-key" not in str(caught.value)
    assert "secret-key" not in caplog.text
    assert len(transport.calls) == (3 if error is ParserUnavailable else 1)


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 0, "data": {"extract_result": []}},
        {"code": 0, "data": {"extract_result": [{}, {}]}},
        {"code": 0, "data": {"extract_result": [None]}},
        {"code": 0, "data": {"extract_result": [{"state": "unknown"}]}},
        {"code": 0, "data": None},
        {},
        {"code": False, "data": {}},
    ],
)
def test_invalid_result_contract_is_rejected(payload):
    transport = _Transport([HttpResponse(200, json.dumps(payload).encode())])
    with pytest.raises(MinerURequestError):
        _client(transport).status("job-1")


@pytest.mark.parametrize(
    "data",
    [
        {"batch_id": "job-1", "file_urls": []},
        {"batch_id": "job-1", "file_urls": ["https://a", "https://b"]},
        {"batch_id": "job-1", "file_urls": ["file:///tmp/local"]},
        {"batch_id": "job-1", "file_urls": ["https://user:password@storage/a"]},
        {"file_urls": ["https://storage/a"]},
    ],
)
def test_invalid_upload_contract_is_rejected_before_put(tmp_path, data):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"pdf")
    transport = _Transport([_response(data)])
    with pytest.raises(MinerURequestError):
        _client(transport).submit(path, "a.pdf", "digest")
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "status,error,attempts",
    [
        (400, MinerURequestError, 1),
        (401, MinerURequestError, 1),
        (403, MinerURequestError, 1),
        (404, MinerUJobLost, 1),
        (429, ParserUnavailable, 3),
        (500, ParserUnavailable, 3),
    ],
)
def test_http_errors_keep_existing_classification(status, error, attempts):
    transport = _Transport([HttpResponse(status, b"secret-key")] * 3)
    with pytest.raises(error) as caught:
        _client(transport).status("job-1")
    assert "secret-key" not in str(caught.value)
    assert len(transport.calls) == attempts


def test_connection_errors_are_retried():
    transport = _Transport([OSError("down"), OSError("down"), _batch("running")])
    assert _client(transport).status("job-1") == MinerUStatus("running")


def test_submit_streams_replayable_raw_file_with_content_length(tmp_path, monkeypatch):
    path = tmp_path / "large.pdf"
    content = b"pdf" * 100_000
    path.write_bytes(content)

    def forbidden_read_bytes(self):
        raise AssertionError("upload must not read the entire file")

    class StreamingTransport(_Transport):
        def send(self, method, url, headers, body, timeout):
            if method == "PUT":
                assert not isinstance(body, bytes)
                chunks = list(body)
                assert max(map(len, chunks)) <= 64 * 1024
                assert b"".join(chunks) == content
            return super().send(method, url, headers, body, timeout)

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    slept = []
    transport = StreamingTransport(
        [
            HttpResponse(429, b""),
            _upload_response(),
            HttpResponse(503, b""),
            HttpResponse(200, b""),
        ]
    )
    assert _client(transport, sleep=slept.append).submit(
        path, "large.pdf", "digest"
    ) == MinerUJob("digest", "job-1")
    assert transport.calls[2].body == transport.calls[3].body == content
    assert transport.calls[2].headers == {"Content-Length": str(len(content))}
    assert slept == [1.0, 1.0]


def test_download_fetches_batch_then_signed_zip_without_credentials(tmp_path):
    url = "https://storage.example/result.zip?signature=private"
    transport = _Transport([_batch(full_zip_url=url), HttpResponse(200, b"PK zip")])
    path = _client(transport).download("job-1", tmp_path)
    assert path.read_bytes() == b"PK zip"
    assert path.name == "job-1.zip"
    assert transport.calls[0].headers["Authorization"] == "Bearer secret-key"
    assert transport.calls[1].url == url
    assert transport.calls[1].headers == {}
    assert transport.calls[1].timeout == 300


@pytest.mark.parametrize(
    "state,extra",
    [
        ("pending", {}),
        ("failed", {"err_msg": "secret-key"}),
        ("done", {}),
        ("done", {"full_zip_url": "file:///tmp/local"}),
    ],
)
def test_download_rejects_unready_or_invalid_results(tmp_path, state, extra):
    transport = _Transport([_batch(state, **extra)])
    with pytest.raises(MinerURequestError):
        _client(transport).download("job-1", tmp_path)
    assert len(transport.calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_download_retries_without_publishing_partial_file(tmp_path):
    class InterruptedTransport(_Transport):
        attempts = 0

        def download(self, url, headers, target, timeout):
            assert not (tmp_path / "job-1.zip").exists()
            assert not target.exists()
            self.attempts += 1
            target.write_bytes(b"partial")
            if self.attempts == 1:
                raise OSError("interrupted")
            target.write_bytes(b"complete")
            return HttpResponse(200, b"")

    transport = InterruptedTransport([_batch(full_zip_url="https://storage/result")])
    path = _client(transport).download("job-1", tmp_path)
    assert path.read_bytes() == b"complete"
    assert list(tmp_path.iterdir()) == [path]


def test_download_failure_cleans_temporary_file_and_preserves_old_result(tmp_path):
    final = tmp_path / "job-1.zip"
    final.write_bytes(b"old")
    transport = _Transport(
        [_batch(full_zip_url="https://storage/result"), HttpResponse(404, b"missing")]
    )
    with pytest.raises(MinerUJobLost):
        _client(transport).download("job-1", tmp_path)
    assert final.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [final]


def test_invalid_job_id_is_rejected_before_request():
    transport = _Transport([])
    with pytest.raises(MinerURequestError):
        _client(transport).status("../job")
    assert transport.calls == []


def test_missing_base_url_is_rejected():
    transport = _Transport([])
    with pytest.raises(ParserUnavailable, match="MINERU_API_URL"):
        _client(transport, base_url="", api_key="").status("job-1")
    assert transport.calls == []


def test_empty_api_key_omits_authorization():
    transport = _Transport([_batch("pending")])
    assert _client(transport, api_key="").status("job-1") == MinerUStatus("queued")
    assert "Authorization" not in transport.calls[0].headers


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
            _batch(full_zip_url="https://storage/result"),
            HttpResponse(503, b"not a zip"),
            HttpResponse(200, b"PK complete"),
        ]
    )
    path = _client(transport).download("job-1", tmp_path)
    assert path.read_bytes() == b"PK complete"
    assert len(transport.calls) == 3
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


def test_urllib_put_omits_implicit_content_type_and_authorization(monkeypatch):
    def open_request(request, timeout):
        # Run urllib's real preprocessing, which normally adds a form Content-Type.
        handler = AbstractHTTPHandler()
        handler.add_parent(build_opener())
        request = handler.do_request_(request)
        assert request.get_method() == "PUT"
        assert request.get_header("Content-type") is None
        assert request.get_header("Authorization") is None
        assert request.get_header("Content-length") == "3"
        assert b"".join(request.data) == b"pdf"
        return io_response()

    monkeypatch.setattr("services.parsing.mineru.client.urlopen", open_request)
    assert UrllibTransport().send(
        "PUT", "https://storage/upload", {"Content-Length": "3"}, [b"pdf"], 300
    ) == HttpResponse(200, b"")


def io_response():
    response = io.BytesIO()
    response.status = 200
    return response


def test_urllib_does_not_forward_control_authorization_on_redirect(monkeypatch):
    def open_request(request, timeout):
        assert request.get_header("Authorization") == "Bearer secret-key"
        redirected = HTTPRedirectHandler().redirect_request(
            request, None, 302, "redirect", {}, "https://storage/result"
        )
        assert redirected.get_header("Authorization") is None
        return io_response()

    monkeypatch.setattr("services.parsing.mineru.client.urlopen", open_request)
    UrllibTransport().send(
        "GET",
        "https://mineru.net/status",
        {"Authorization": "Bearer secret-key"},
        None,
        60,
    )


def test_status_redacts_key_from_failure_message():
    transport = _Transport([_batch("failed", err_msg="problem secret-key")])
    assert _client(transport).status("job-1").error == "problem [redacted]"


def test_upload_file_change_is_rejected_before_streaming(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"pdf")

    class MutatingTransport(_Transport):
        def send(self, method, url, headers, body, timeout):
            if method == "PUT":
                path.write_bytes(b"changed")
            return super().send(method, url, headers, body, timeout)

    transport = MutatingTransport([_upload_response()])
    with pytest.raises(MinerURequestError, match="大小已改变"):
        _client(transport).submit(path, "a.pdf", "digest")
