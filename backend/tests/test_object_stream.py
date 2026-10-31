# -*- coding: utf-8 -*-
"""S3 response streams stay lazy and close at every context exit."""

import io
from contextlib import contextmanager

import pytest
from botocore.exceptions import ClientError
from botocore.exceptions import EndpointConnectionError
from botocore.exceptions import IncompleteReadError
from botocore.exceptions import ReadTimeoutError
from botocore.exceptions import ResponseStreamingError

from services.common.exceptions import StorageError
from services.common.object_storage import ObjectStorage
from services.common.object_storage import S3ObjectStorage
from services.parsing.adapters import object_reader


class FakeBody(io.BytesIO):
    def __init__(self, error=None):
        super().__init__(b"streamed content")
        self.error = error
        self.read_sizes = []
        self.close_count = 0

    def read(self, size=-1):
        self.read_sizes.append(size)
        if self.error is not None and len(self.read_sizes) > 1:
            raise self.error
        return super().read(size)

    def close(self):
        self.close_count += 1
        super().close()


class FakeClient:
    def __init__(self, body=None, error=None):
        self.body = body
        self.error = error
        self.requests = []

    def get_object(self, **kwargs):
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"Body": self.body}


def make_storage(body=None, error=None):
    storage = S3ObjectStorage.__new__(S3ObjectStorage)
    storage.bucket = "test-bucket"
    storage._client = FakeClient(body, error)
    return storage


def client_error(code):
    return ClientError({"Error": {"Code": code}}, "GetObject")


def test_storage_interface_declares_open_object():
    with pytest.raises(NotImplementedError):
        ObjectStorage().open_object("key")


def test_stream_is_lazy_and_closes_after_bounded_reads():
    body = FakeBody()
    storage = make_storage(body)
    context = storage.open_object("nested/key")
    assert storage._client.requests == []
    with context as stream:
        assert stream is body
        assert body.read_sizes == []
        assert stream.read(8) == b"streamed"
        assert stream.read(8) == b" content"
        assert stream.read(8) == b""
        assert not body.closed
    assert body.closed
    assert body.close_count == 1
    assert body.read_sizes == [8, 8, 8]
    assert storage._client.requests == [{"Bucket": "test-bucket", "Key": "nested/key"}]


def test_stream_closes_after_early_abort():
    body = FakeBody()
    with make_storage(body).open_object("key") as stream:
        assert stream.read(1) == b"s"
    assert body.closed
    assert body.close_count == 1
    assert body.read_sizes == [1]


@pytest.mark.parametrize("error", [ValueError("consumer"), RuntimeError("consumer")])
def test_stream_preserves_consumer_exception_and_closes(error):
    body = FakeBody()
    with pytest.raises(type(error)) as caught:
        with make_storage(body).open_object("key") as stream:
            stream.read(1)
            raise error
    assert caught.value is error
    assert body.closed
    assert body.close_count == 1


@pytest.mark.parametrize("code", ["NoSuchKey", "404", "NotFound"])
def test_missing_object_errors_are_normalized(code):
    error = client_error(code)
    with pytest.raises(StorageError, match="对象不存在") as caught:
        with make_storage(error=error).open_object("missing"):
            pytest.fail("missing object yielded a stream")
    assert caught.value.__cause__ is error


@pytest.mark.parametrize(
    "error",
    [client_error("AccessDenied"), EndpointConnectionError(endpoint_url="https://s3")],
)
def test_acquisition_errors_are_normalized(error):
    with pytest.raises(StorageError, match="对象存储读取失败") as caught:
        with make_storage(error=error).open_object("key"):
            pytest.fail("failed request yielded a stream")
    assert caught.value.__cause__ is error


def test_missing_response_body_is_rejected():
    with pytest.raises(StorageError, match="对象存储返回空响应"):
        with make_storage().open_object("key"):
            pytest.fail("empty response yielded a stream")


@pytest.mark.parametrize(
    "error",
    [
        ReadTimeoutError(endpoint_url="https://s3"),
        ResponseStreamingError(error="connection lost"),
        IncompleteReadError(actual_bytes=1, expected_bytes=16),
        client_error("AccessDenied"),
    ],
)
def test_mid_stream_errors_are_normalized_and_body_closes(error):
    body = FakeBody(error)
    with pytest.raises(StorageError, match="对象存储读取失败") as caught:
        with make_storage(body).open_object("key") as stream:
            assert stream.read(1) == b"s"
            stream.read(1)
    assert caught.value.__cause__ is error
    assert body.closed
    assert body.close_count == 1


def test_get_object_preserves_bytes_result_and_closes_body():
    body = FakeBody()
    assert make_storage(body).get_object("key") == b"streamed content"
    assert body.closed
    assert body.close_count == 1


def test_reader_forwards_stream_context(monkeypatch):
    body = FakeBody()
    keys = []

    class Storage:
        @contextmanager
        def open_object(self, key):
            keys.append(key)
            try:
                yield body
            finally:
                body.close()

    monkeypatch.setattr(object_reader, "storage_client", Storage)
    with object_reader.StorageObjectReader().open_object("parsed.md") as stream:
        assert stream is body
        assert stream.read(8) == b"streamed"
    assert keys == ["parsed.md"]
    assert body.closed
