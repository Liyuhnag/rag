# -*- coding: utf-8 -*-
"""本地解析块只消费一次，并保留原文续片。"""

import json

import pytest

from services.parsing.local_artifacts import store_document
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument
from datetime import datetime
from datetime import timezone


class Store:
    def __init__(self):
        self.objects = {}
        self.paths = []

    def upload_file(self, key, path, content_type):
        self.paths.append(path)
        self.objects[key] = path.read_bytes()

    def write_object(self, key, content, content_type):
        self.objects[key] = content


def document(blocks, file_type="txt"):
    return ParsedDocument(
        ParseRequest("doc", 1, "source", file_type, "demo.txt", 1),
        "demo",
        blocks=blocks,
    )


def test_blocks_are_single_pass_and_continuations_are_joined():
    consumed = []

    def blocks():
        for block in [
            ParsedBlock("text", "# 标题"),
            ParsedBlock("text", "前半"),
            ParsedBlock("text", "后半", metadata={"continuation": True}),
        ]:
            consumed.append(block.text)
            yield block

    store = Store()
    keys = store_document(document(blocks()), store, datetime.now(timezone.utc))
    assert consumed == ["# 标题", "前半", "后半"]
    assert store.objects[keys.md_key].decode() == "# 标题\n\n前半后半\n"
    payload = json.loads(store.objects[keys.json_key])
    assert payload["blocks"][2]["metadata"]["continuation"] is True
    assert all(not path.exists() for path in store.paths)


def test_markdown_chunks_preserve_exact_bytes():
    blocks = iter(
        [
            ParsedBlock("text", "# 标题\r\n", metadata={"verbatim": True}),
            ParsedBlock("text", "  正文  ", metadata={"verbatim": True}),
        ]
    )
    store = Store()
    keys = store_document(document(blocks, "md"), store, datetime.now(timezone.utc))
    assert store.objects[keys.md_key].decode() == "# 标题\r\n  正文  "


def test_failed_iteration_closes_source_and_does_not_upload():
    closed = []

    def blocks():
        try:
            yield ParsedBlock("text", "正文")
            raise ValueError("bad block")
        finally:
            closed.append(True)

    store = Store()
    with pytest.raises(ValueError, match="bad block"):
        store_document(document(blocks()), store, datetime.now(timezone.utc))
    assert closed == [True]
    assert store.objects == {}


def test_attempt_outputs_cannot_overwrite_another_claim():
    store = Store()
    now = datetime.now(timezone.utc)
    new = store_document(document([ParsedBlock("text", "new")]), store, now, "new-token")
    saved = dict(store.objects)
    old = store_document(document([ParsedBlock("text", "late-old")]), store, now, "old-token")
    assert old.md_key != new.md_key
    assert all(store.objects[key] == value for key, value in saved.items())
    manifest = json.loads(store.objects[new.manifest_key])
    assert manifest["merged_md_key"] == new.md_key
    assert manifest["merged_json_key"] == new.json_key
