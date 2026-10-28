# -*- coding: utf-8 -*-
"""字段推断只使用块内 JSON 类型，跨块只保留列统计。"""

import json
from contextlib import contextmanager
from io import BytesIO
from itertools import permutations
from types import SimpleNamespace

import pytest

from services.chunking.source import StoredParsedSource
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument
from services.structured.service import StructuredSchemaService
from services.structured.service import _datasets


def parsed(blocks, version=1):
    return ParsedDocument(
        ParseRequest("doc", version, "source", "csv", "a.csv", 10), "a", blocks
    )


def block(rows, sheet="a", **metadata):
    return ParsedBlock("table", json.dumps(rows), sheet=sheet, metadata=metadata)


def extract(blocks, version=1):
    return list(_datasets(parsed(blocks, version), "md", "json"))


@pytest.mark.parametrize(
    "values,kind",
    [
        ([None, 1, 2.5], "number"),
        ([None, None], "null"),
        ([True, False, None], "boolean"),
        ([True, 1, None], "mixed"),
        (["2026-01-02", "2026-01-02T03:04:05", None], "string"),
        (["1", 1, None], "mixed"),
    ],
)
def test_type_merge_is_order_independent(values, kind):
    for ordered in permutations(values):
        dataset = extract(block([{"value": value}]) for value in ordered)[0]
        assert dataset.schema_json == [
            {"name": "value", "type": kind, "nullable": True}
        ]
        assert dataset.row_count == len(values)


def test_multiple_blocks_and_interleaved_sheets_count_rows_not_metadata():
    datasets = extract(
        [
            block([{"a": 1}], row_count=999),
            block([{"b": False}], sheet="other", row_count=999),
            block([{"a": 2.5, "late": "yes"}, {"a": None}], row_count=999),
        ]
    )
    assert [dataset.sheet_name for dataset in datasets] == ["a", "other"]
    assert datasets[0].row_count == 3
    assert datasets[0].schema_json == [
        {"name": "a", "type": "number", "nullable": True},
        {"name": "late", "type": "string", "nullable": True},
    ]
    assert datasets[1].schema_json == [
        {"name": "b", "type": "boolean", "nullable": False}
    ]
    assert datasets[1].row_count == 1


def test_empty_sheet_headers_preserved_and_old_artifact_requires_reparse():
    dataset = extract([block([], headers=["name", "amount"])])[0]
    assert dataset.row_count == 0
    assert dataset.schema_json == [
        {"name": "name", "type": "null", "nullable": True},
        {"name": "amount", "type": "null", "nullable": True},
    ]
    with pytest.raises(ValueError, match="重新解析"):
        extract([block([], column_count=2)])


def test_schema_and_ids_replay_deterministically_and_version_changes_identity():
    blocks = [block([{"a": 1}]), block([{"b": None}], sheet="other")]
    first = extract(blocks)
    assert first == extract(blocks)
    assert first[0].id != first[1].id
    assert first[0].id != extract(blocks, version=2)[0].id
    assert first[0].storage_table is None
    assert first[0].status.value == "SCHEMA_READY"
    assert first[0].source_json_key == "json"


@pytest.mark.parametrize(
    "blocks,match",
    [
        ([block([], headers=[str(index) for index in range(257)])], "256"),
        ([block([{"a": 1}], sheet=str(index)) for index in range(51)], "50"),
        ([block([{"a": {"nested": 1}}])], "标量"),
        ([block([{"a": float("nan")}])], "非有限"),
        ([block([1])], "对象"),
        ([ParsedBlock("table", "{}", sheet="a")], "数组"),
    ],
)
def test_malformed_artifacts_rejected(blocks, match):
    with pytest.raises(ValueError, match=match):
        extract(blocks)


def test_actual_stored_source_stream_replay_and_context_closure(monkeypatch):
    payload = json.dumps(
        {
            "blocks": [
                {"kind": "table", "text": '[{"amount": 1}]', "sheet": "sales"},
                {"kind": "table", "text": '[{"amount": 2.5}]', "sheet": "sales"},
                {
                    "kind": "table",
                    "text": "[]",
                    "sheet": "empty",
                    "metadata": {"headers": ["a"]},
                },
            ]
        }
    ).encode()
    streams = []

    class Reader:
        @contextmanager
        def open_object(self, key):
            assert key == "json"
            with BytesIO(payload) as stream:
                streams.append(stream)
                yield stream

    snapshots = []

    def replace(document_id, version, datasets):
        snapshots.append(list(datasets))
        return True

    monkeypatch.setattr(
        "services.structured.service.orm.replace_current_version", replace
    )
    document = SimpleNamespace(
        id="doc",
        name="a.xlsx",
        file_type="xlsx",
        object_key="source",
        size_bytes=10,
        parse_version=1,
    )
    service = StructuredSchemaService(StoredParsedSource(Reader()))
    assert service.run(document, 1, "md", "json")
    assert service.run(document, 1, "md", "json")
    assert snapshots[0] == snapshots[1]
    assert snapshots[0][0].row_count == 2
    assert snapshots[0][0].schema_json == [
        {"name": "amount", "type": "number", "nullable": False}
    ]
    assert all(stream.closed for stream in streams)
    document.parse_version = 2
    assert service.run(document, 1, "md", "json") is False
    assert len(streams) == 2
