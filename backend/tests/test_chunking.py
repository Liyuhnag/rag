# -*- coding: utf-8 -*-
"""Business mapping, bounded consumption and conservative source provenance."""

import json

import pytest
import tiktoken

from model.document_chunk.model import ChunkLevel
from services.chunking.common.errors import ChunkingFailed
from services.chunking.common.models import ChunkOptions
from services.chunking.token import TokenChunker
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument


def document(blocks, version=2, file_type="md"):
    return ParsedDocument(
        ParseRequest("doc", version, "source", file_type, "guide.md", 1),
        "Guide",
        blocks,
    )


def test_business_mapping_and_deterministic_versioned_identity():
    blocks = [
        ParsedBlock("text", "你好 world", page=3, locator="p3", heading_path="A / B")
    ]
    chunks = TokenChunker().split(document(blocks), ChunkOptions())
    parent, child = chunks
    assert parent.chunk_level == ChunkLevel.PARENT
    assert child.chunk_level == ChunkLevel.CHILD
    assert parent.content == child.content == "你好 world"
    assert parent.token_count == len(
        tiktoken.get_encoding("cl100k_base").encode_ordinary(parent.content)
    )
    assert parent.document_id == child.document_id == "doc"
    assert parent.version == child.version == 2
    assert parent.parent_index == child.parent_index == 0
    assert parent.child_order is None and child.child_order == 0
    assert parent.chunk_index == 0 and child.chunk_index == 1
    assert parent.child_count == 1 and child.child_count == 0
    assert parent.parent_id is None and child.parent_id == parent.id
    assert parent.file_type == child.file_type == "md"
    assert parent.page_start == parent.page_end == child.page_start == 3
    assert parent.locator == child.locator == "p3"
    assert parent.heading_path == child.heading_path == "A / B"
    assert chunks == TokenChunker().split(document(blocks), ChunkOptions())
    assert (
        parent.id
        != TokenChunker().split(document(blocks, version=3), ChunkOptions())[0].id
    )


def test_native_hierarchy_emits_each_parent_before_its_children():
    options = ChunkOptions(parent_chunk_size=100, child_chunk_size=30, chunk_overlap=8)
    text = "A sentence has several words. " * 100
    chunks = TokenChunker().split(
        document([ParsedBlock("text", text, page=7)]), options
    )
    parents = [chunk for chunk in chunks if chunk.chunk_level == ChunkLevel.PARENT]
    assert len(parents) > 1
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert [parent.parent_index for parent in parents] == list(range(len(parents)))
    for parent in parents:
        children = [chunk for chunk in chunks if chunk.parent_id == parent.id]
        assert parent.child_count == len(children) > 0
        assert parent.token_count <= options.parent_chunk_size
        assert all(child.token_count <= options.child_chunk_size for child in children)
        assert all(child.chunk_index > parent.chunk_index for child in children)
        assert [child.child_order for child in children] == list(range(len(children)))
    assert all(chunk.page_start == chunk.page_end == 7 for chunk in chunks)


def test_grouping_merges_lines_but_keeps_distinct_pages_and_unknown_pages():
    blocks = [
        ParsedBlock(
            "text", "hello\n", page=1, locator="line:1", metadata={"verbatim": True}
        ),
        ParsedBlock(
            "text", "world", page=1, locator="line:2", metadata={"verbatim": True}
        ),
        ParsedBlock("text", "next", page=2, locator="p2"),
        ParsedBlock("text", "unknown", locator="unmeasured"),
    ]
    chunks = TokenChunker().split(document(blocks), ChunkOptions())
    parents = [chunk for chunk in chunks if chunk.chunk_level == ChunkLevel.PARENT]
    assert [parent.content for parent in parents] == ["hello\nworld", "next", "unknown"]
    assert parents[0].locator == "line:1; line:2"
    assert parents[0].page_start == parents[0].page_end == 1
    assert parents[1].page_start == parents[1].page_end == 2
    assert parents[2].page_start is parents[2].page_end is None


def test_large_block_children_keep_source_locator_without_fabricated_offsets():
    chunks = TokenChunker().split(
        document(
            [ParsedBlock("text", "word " * 500, sheet="Data", locator="rows:1-20")]
        ),
        ChunkOptions(parent_chunk_size=100, child_chunk_size=30, chunk_overlap=5),
    )
    assert len(chunks) > 5
    assert all(chunk.locator == "sheet:Data/rows:1-20" for chunk in chunks)
    assert all(chunk.page_start is None for chunk in chunks)


def test_iterator_consumes_only_one_bounded_group_before_emitting():
    consumed = []

    def blocks():
        for index in range(1000):
            consumed.append(index)
            yield ParsedBlock("text", "line\n", metadata={"verbatim": True})

    iterator = TokenChunker().iter_split(document(blocks()), ChunkOptions())
    assert next(iterator).chunk_level == ChunkLevel.PARENT
    assert len(consumed) <= 129
    assert len(list(iterator)) > 2


@pytest.mark.parametrize("file_type", ["csv", "xlsx"])
def test_json_tables_keep_json_content_and_preserve_sheet(file_type):
    records = [{"Name": "Alice", "Value": 1}, {"Name": "Bob", "Value": 2}]
    chunks = TokenChunker().split(
        document(
            [
                ParsedBlock(
                    "table", json.dumps(records), sheet="Data", locator="rows:1-2"
                )
            ],
            file_type=file_type,
        ),
        ChunkOptions(),
    )
    assert chunks[0].content == json.dumps(records)
    assert json.loads(chunks[0].content) == records
    assert all(chunk.locator == "sheet:Data/rows:1-2" for chunk in chunks)
    assert all(chunk.file_type == file_type for chunk in chunks)


def test_empty_and_whitespace_blocks_produce_no_chunks():
    assert TokenChunker().split(document([]), ChunkOptions()) == []
    assert (
        TokenChunker().split(document([ParsedBlock("text", " \n\t")]), ChunkOptions())
        == []
    )


def test_locator_limit_flushes_without_truncation_or_invented_ranges():
    blocks = [
        ParsedBlock("text", "text", locator=str(index) * 200) for index in range(3)
    ]
    chunks = TokenChunker().split(document(blocks), ChunkOptions())
    assert all(len(chunk.locator) <= 512 for chunk in chunks)
    locators = set()
    for chunk in chunks:
        locators.update(chunk.locator.split("; "))
    assert locators == {block.locator for block in blocks}
    with pytest.raises(ChunkingFailed, match="locator"):
        TokenChunker().split(
            document([ParsedBlock("text", "text", locator="x" * 513)]), ChunkOptions()
        )


def test_block_stream_errors_propagate_without_consuming_entire_document():
    def blocks():
        yield ParsedBlock("text", "text", page=1)
        yield ParsedBlock("text", "next", page=2)
        raise RuntimeError("source failed")

    iterator = TokenChunker().iter_split(document(blocks()), ChunkOptions())
    assert next(iterator).content == "text"
    assert next(iterator).content == "text"
    with pytest.raises(RuntimeError, match="source failed"):
        list(iterator)
