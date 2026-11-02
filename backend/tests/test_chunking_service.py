# -*- coding: utf-8 -*-
"""从持久化解析结果流式读取块。"""

import json
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

from services.indexing.chunking.source import StoredParsedSource


def test_artifact_source_reads_local_blocks_with_provenance():
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
