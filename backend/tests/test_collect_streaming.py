# -*- coding: utf-8 -*-
"""大产物只经磁盘传递，失败不发布完成清单。"""

import zipfile
from pathlib import Path

import pytest

from services.parsing.collect import PartZip
from services.parsing.service import DocumentParsingService


class DiskStore:
    def __init__(self, root: Path):
        self.root = root
        self.objects = {}
        self.downloads = []

    def read_object(self, key):
        raise AssertionError("must not read complete objects")

    def download_object(self, key, path):
        self.downloads.append(key)
        with self.objects[key].open("rb") as source, path.open("wb") as target:
            import shutil

            shutil.copyfileobj(source, target, 65536)

    def upload_file(self, key, path, content_type):
        import shutil

        target = self.root / str(len(self.objects))
        shutil.copyfile(path, target)
        self.objects[key] = target

    def write_object(self, key, content, content_type):
        assert len(content) < 65536
        target = self.root / str(len(self.objects))
        target.write_bytes(content)
        self.objects[key] = target


def test_collect_downloads_keys_and_uploads_files(tmp_path):
    store = DiskStore(tmp_path)
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("content.md", "正文\n\n![图](images/a.png)".encode())
        archive.writestr("images/a.png", b"image")
    store.objects["zip-key"] = source
    result = DocumentParsingService(store).collect(
        "doc", 1, "pdf", "original", [PartZip(1, 1, 2, "zip-key")]
    )
    assert store.downloads == ["zip-key"]
    assert store.objects[result.md_key].read_text() == (
        "<!-- page: 1 -->\n\n正文\n\n![图](assets/part-1/images/a.png)\n"
    )
    assert store.objects[result.manifest_key].exists()


def test_invalid_utf8_does_not_publish_manifest(tmp_path):
    store = DiskStore(tmp_path)
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("content.md", b"\xff")
    store.objects["zip-key"] = source
    with pytest.raises(Exception, match="UTF-8"):
        DocumentParsingService(store).collect(
            "doc", 1, "pdf", "original", [PartZip(1, 1, 1, "zip-key")]
        )
    assert "documents/doc/parsed/1/manifest.json" not in store.objects


def test_later_invalid_part_does_not_publish_any_artifacts(tmp_path):
    store = DiskStore(tmp_path)
    for key, content in (("first", b"valid"), ("second", b"\xff")):
        path = tmp_path / (key + ".zip")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("content.md", content)
        store.objects[key] = path
    with pytest.raises(Exception, match="UTF-8"):
        DocumentParsingService(store).collect(
            "doc",
            1,
            "pdf",
            "original",
            [PartZip(1, 1, 1, "first"), PartZip(2, 2, 2, "second")],
        )
    assert set(store.objects) == {"first", "second"}


def test_member_actual_bytes_are_limited(tmp_path, monkeypatch):
    import services.parsing.collect as collect

    monkeypatch.setattr(collect, "_MAX_BYTES", 5)
    store = DiskStore(tmp_path)
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("content.md", b"123456")
    store.objects["zip-key"] = source
    with pytest.raises(Exception, match="过大"):
        DocumentParsingService(store).collect(
            "doc", 1, "pdf", "original", [PartZip(1, 1, 1, "zip-key")]
        )
    assert set(store.objects) == {"zip-key"}


def test_long_line_is_rejected_without_publishing(tmp_path):
    store = DiskStore(tmp_path)
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("content.md", b"![" + b"x" * (1024 * 1024 + 1))
    store.objects["zip-key"] = source
    with pytest.raises(Exception, match="图片链接过长"):
        DocumentParsingService(store).collect(
            "doc", 1, "pdf", "original", [PartZip(1, 1, 1, "zip-key")]
        )
    assert set(store.objects) == {"zip-key"}


def test_multiline_image_and_unicode_whitespace_at_buffer_boundary(tmp_path):
    store = DiskStore(tmp_path)
    source = tmp_path / "source.zip"
    text = "a" * 65530 + "![描述\n文字](images/a.png)" + "\u3000" * 25000
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("content.md", text.encode())
        archive.writestr("images/a.png", b"image")
    store.objects["zip-key"] = source
    result = DocumentParsingService(store).collect(
        "doc", 1, "pdf", "original", [PartZip(1, 1, 1, "zip-key")]
    )
    output = store.objects[result.md_key].read_text()
    assert (
        output
        == "<!-- page: 1 -->\n\n"
        + "a" * 65530
        + "![描述\n文字](assets/part-1/images/a.png)\n"
    )


def test_missing_later_zip_does_not_publish_any_artifacts(tmp_path):
    store = DiskStore(tmp_path)
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("content.md", b"valid")
    store.objects["first"] = source
    with pytest.raises(KeyError):
        DocumentParsingService(store).collect(
            "doc",
            1,
            "pdf",
            "original",
            [PartZip(1, 1, 1, "first"), PartZip(2, 2, 2, "missing")],
        )
    assert set(store.objects) == {"first"}


def test_zip_with_too_many_ignored_entries_is_rejected(tmp_path, monkeypatch):
    import services.parsing.collect as collect

    monkeypatch.setattr(collect, "_MAX_FILES", 2)
    store = DiskStore(tmp_path)
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("content.md", b"valid")
        archive.writestr("one.exe", b"")
        archive.writestr("two.exe", b"")
    store.objects["zip-key"] = source
    with pytest.raises(Exception, match="文件过多"):
        DocumentParsingService(store).collect(
            "doc", 1, "pdf", "original", [PartZip(1, 1, 1, "zip-key")]
        )


def test_s3_file_upload_passes_key_and_content_type(tmp_path):
    from services.common.object_storage import S3ObjectStorage

    class Client:
        def upload_file(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    storage = object.__new__(S3ObjectStorage)
    storage.bucket = "bucket"
    storage._client = Client()
    path = tmp_path / "output.md"
    path.write_text("hello")
    storage.upload_file("key", str(path), "text/markdown")
    assert storage._client.args == (str(path), "bucket", "key")
    assert storage._client.kwargs["ExtraArgs"] == {"ContentType": "text/markdown"}
    config = storage._client.kwargs["Config"]
    assert config.max_concurrency == 1
    assert config.use_threads is False


def test_s3_file_upload_wraps_transfer_failure(tmp_path):
    from boto3.exceptions import S3UploadFailedError
    from services.common.exceptions import StorageError
    from services.common.object_storage import S3ObjectStorage

    class Client:
        def upload_file(self, *args, **kwargs):
            raise S3UploadFailedError("failed")

    storage = object.__new__(S3ObjectStorage)
    storage.bucket = "bucket"
    storage._client = Client()
    with pytest.raises(StorageError, match="写入失败"):
        storage.upload_file("key", str(tmp_path / "missing"), "text/markdown")


def test_zip64_directory_is_explicitly_rejected(tmp_path):
    from services.parsing.collect import _check_archive_directory
    from services.parsing.errors import ParseFailed
    import struct

    path = tmp_path / "zip64.zip"
    path.write_bytes(
        struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0, 0, 0)
    )
    with pytest.raises(ParseFailed, match="ZIP64"):
        _check_archive_directory(path)


def test_leading_whitespace_longer_than_buffer_preserves_text(tmp_path):
    store = DiskStore(tmp_path)
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("content.md", (" " * 150000 + "正文").encode())
    store.objects["zip-key"] = source
    result = DocumentParsingService(store).collect(
        "doc", 1, "pdf", "original", [PartZip(1, 1, 1, "zip-key")]
    )
    assert store.objects[result.md_key].read_text() == "<!-- page: 1 -->\n\n正文\n"


def test_multiple_buffer_lengths_unicode_trailing_whitespace(tmp_path):
    store = DiskStore(tmp_path)
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("content.md", ("正文" + "\u3000" * 100000).encode())
    store.objects["zip-key"] = source
    result = DocumentParsingService(store).collect(
        "doc", 1, "pdf", "original", [PartZip(1, 1, 1, "zip-key")]
    )
    assert store.objects[result.md_key].read_text() == "<!-- page: 1 -->\n\n正文\n"
