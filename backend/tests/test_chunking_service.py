# -*- coding: utf-8 -*-
"""从持久化解析结果生成可重放的父子块。"""

import json
from io import BytesIO
from contextlib import contextmanager

from model.document_chunk.model import ChunkLevel
from services.chunking.service import DocumentChunkingService
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument


class MemorySource:
    @contextmanager
    def read(self, document, version, md_key, json_key):
        yield ParsedDocument(
            ParseRequest(document.id, version, "source", "txt", "a.txt", 10),
            "a",
            [ParsedBlock(kind="text", text="已完成解析的正文。")],
        )


class MemoryStore:
    def __init__(self):
        self.chunks = []

    def replace_for_version(self, document_id, version, chunks):
        self.chunks = list(chunks)
        return True


def test_service_replaces_same_version_without_duplicates(monkeypatch):
    from types import SimpleNamespace

    store = MemoryStore()
    monkeypatch.setattr(
        "services.chunking.service.chunk_orm.replace_current_version",
        store.replace_for_version,
    )
    service = DocumentChunkingService(source=MemorySource())
    document = SimpleNamespace(id="doc", parse_version=1)
    service.run(document, 1, "parsed.md", "parsed.json")
    first = [chunk.id for chunk in store.chunks]
    service.run(document, 1, "parsed.md", "parsed.json")
    assert [chunk.id for chunk in store.chunks] == first
    assert len(store.chunks) == 2
    assert store.chunks[0].chunk_level == ChunkLevel.PARENT
    assert store.chunks[1].parent_id == store.chunks[0].id


def test_artifact_source_reads_local_blocks_with_provenance():
    from types import SimpleNamespace
    from services.chunking.source import StoredParsedSource

    payload = {
        "type": "txt",
        "title": "a",
        "blocks": [{"kind": "text", "text": "正文", "page": 3, "locator": "p:3"}],
    }

    class Reader:
        @contextmanager
        def open_object(self, key):
            with BytesIO(json.dumps(payload).encode("utf-8")) as stream:
                yield stream

    document = SimpleNamespace(
        id="doc",
        name="a.txt",
        file_type="txt",
        object_key="source",
        size_bytes=10,
    )
    with StoredParsedSource(Reader()).read(document, 1, "md", "json") as parsed:
        blocks = list(parsed.blocks)
    assert blocks[0].text == "正文"
    assert blocks[0].page == 3
    assert blocks[0].locator == "p:3"
