# -*- coding: utf-8 -*-
"""收集分段结果，合并后写入对象存储。"""

import io
import json
import zipfile
from pathlib import Path

import pytest

from services.parsing.collect import PartZip
from services.parsing.errors import ParseFailed
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument
from services.parsing.service import DocumentParsingService
import services.parsing.service as parsing_service


class _Store:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def read_object(self, key: str) -> bytes:
        return self.objects[key][0]

    def download_object(self, key: str, path: Path) -> None:
        path.write_bytes(self.read_object(key))

    def upload_file(self, key: str, path: Path, content_type: str) -> None:
        self.write_object(key, path.read_bytes(), content_type)

    def write_object(self, key: str, content: bytes, content_type: str) -> None:
        self.objects[key] = (content, content_type)


def test_official_zip_prefers_full_markdown_and_preserves_json_artifacts():
    store = _Store()
    store.objects["official.zip"] = (
        _zip(
            {
                "full.md": b"Official parsed content",
                "readme.md": b"unrelated " * 100,
                "demo_content_list.json": b'[{"type":"text"}]',
                "layout.json": b'{"pdf_info":[]}',
            }
        ),
        "application/zip",
    )
    result = DocumentParsingService(reader=store).collect(
        "doc-1", 1, "pdf", "source.pdf", [PartZip(1, 1, 1, "official.zip")]
    )
    assert b"Official parsed content" in store.objects[result.md_key][0]
    assert b"unrelated" not in store.objects[result.md_key][0]
    manifest_key = dict(result.part_results)[1]
    manifest = json.loads(store.objects[manifest_key][0])
    assert len(manifest["json_keys"]) == 2
    for key in manifest["json_keys"]:
        assert store.objects[key][1] == "application/json"
    assert json.loads(store.objects[manifest["json_keys"][0]][0]) == [{"type": "text"}]


def test_mineru_parts_are_merged_and_stored():
    store = _Store()
    service = DocumentParsingService(reader=store)
    store.objects["part2"] = (
        _zip(
            {
                "content.md": "后半\n\n![图](images/b.png)".encode(),
                "images/b.png": b"png-b",
            }
        ),
        "application/zip",
    )
    store.objects["part1"] = (
        _zip(
            {
                "auto/demo.md": "前半\n\n![图](images/a.png)".encode(),
                "auto/images/a.png": b"png-a",
                "auto/content.json": b"{}",
            }
        ),
        "application/zip",
    )
    result = service.collect(
        "doc-1",
        2,
        "pdf",
        "documents/doc-1/source/demo.pdf",
        [
            PartZip(
                part_index=2,
                page_start=3,
                page_end=4,
                result_key="part2",
            ),
            PartZip(
                part_index=1,
                page_start=1,
                page_end=2,
                result_key="part1",
            ),
        ],
    )

    assert result.md_key == "documents/doc-1/parsed/2/merged.md"
    assert result.json_key == "documents/doc-1/parsed/2/merged.json"
    assert store.objects[result.md_key][0].decode() == (
        "<!-- page: 1 -->\n\n"
        "前半\n\n"
        "![图](assets/part-1/images/a.png)\n\n"
        "<!-- page: 3 -->\n\n"
        "后半\n\n"
        "![图](assets/part-2/images/b.png)\n"
    )
    assert (
        store.objects["documents/doc-1/parsed/2/assets/part-1/images/a.png"][0]
        == b"png-a"
    )
    assert (
        store.objects["documents/doc-1/parsed/2/assets/part-2/images/b.png"][0]
        == b"png-b"
    )
    assert result.part_results == (
        (
            2,
            "documents/doc-1/parse/2/parts/2/manifest.json",
        ),
        (
            1,
            "documents/doc-1/parse/2/parts/1/manifest.json",
        ),
    )
    saved = json.loads(store.objects[result.json_key][0])
    assert saved["page_count"] == 4
    assert store.objects["documents/doc-1/parse/2/parts/1/content.json"][0] == b"{}"


def test_local_document_is_stored_without_page_markers():
    store = _Store()
    parsed = ParsedDocument(
        request=ParseRequest(
            document_id="doc-1",
            version=1,
            object_key="documents/doc-1/demo.md",
            file_type="md",
            filename="demo.md",
            size_bytes=4,
        ),
        title="demo",
        blocks=[
            ParsedBlock(kind="text", text="# 标题"),
            ParsedBlock(kind="text", text="正文"),
        ],
    )

    result = DocumentParsingService(reader=store).upload(parsed)

    assert store.objects[result.md_key][0].decode() == "# 标题\n\n正文\n"
    saved = json.loads(store.objects[result.json_key][0])
    assert saved["blocks"][1]["text"] == "正文"


def test_persist_records_artifact_keys(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        parsing_service.document_orm,
        "set_parsed_artifacts",
        lambda document_id, md_key, json_key, now, **kwargs: recorded.append(
            (document_id, md_key, json_key, kwargs["expected_version"])
        ),
    )
    parsed = ParsedDocument(
        request=ParseRequest(
            document_id="doc-1",
            version=1,
            object_key="documents/doc-1/demo.md",
            file_type="md",
            filename="demo.md",
            size_bytes=1,
        ),
        title="demo",
    )

    DocumentParsingService(reader=_Store()).persist(
        parsed,
        parsing_service.StoredParseResult("md-key", "json-key"),
    )

    assert recorded == [("doc-1", "md-key", "json-key", 1)]


def test_unsafe_zip_is_rejected():
    store = _Store()
    store.objects["zip"] = (_zip({"../evil.md": b"x"}), "application/zip")
    service = DocumentParsingService(reader=store)

    with pytest.raises(ParseFailed, match="路径不安全"):
        service.collect(
            "doc-1",
            1,
            "pdf",
            "documents/doc-1/source/demo.pdf",
            [PartZip(1, 1, 1, "zip")],
        )


def test_zip_without_markdown_is_rejected():
    store = _Store()
    store.objects["zip"] = (_zip({"images/a.png": b"png"}), "application/zip")
    service = DocumentParsingService(reader=store)

    with pytest.raises(ParseFailed, match="没有 Markdown"):
        service.collect(
            "doc-1",
            1,
            "pdf",
            "documents/doc-1/source/demo.pdf",
            [PartZip(1, 1, 1, "zip")],
        )


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_mineru_attempt_outputs_and_manifest_references_are_isolated():
    store = _Store()
    store.objects["part"] = (
        _zip({"full.md": b"![image](images/a.png)", "images/a.png": b"png", "layout.json": b"{}"}),
        "application/zip",
    )
    parts = [PartZip(1, 1, 1, "part")]
    new = DocumentParsingService(store, attempt_id="new-token").collect("doc", 1, "pdf", "source", parts)
    saved = dict(store.objects)
    old = DocumentParsingService(store, attempt_id="old-token").collect("doc", 1, "pdf", "source", parts)
    assert old.md_key != new.md_key
    assert all(store.objects[key] == value for key, value in saved.items())
    manifest = json.loads(store.objects[new.manifest_key][0])
    assert manifest["merged_md_key"] == new.md_key
    part = json.loads(store.objects[manifest["parts"][0]["result_key"]][0])
    for key in [part["markdown_key"], *part["images"], *part["json_keys"]]:
        assert "/attempts/new-token/" in key
        assert key in store.objects
