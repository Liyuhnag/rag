# -*- coding: utf-8 -*-
"""Verify native parser delegation and offline tokenizer behavior."""

import nltk
import pytest
import requests
from llama_index.core.node_parser import HierarchicalNodeParser
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import Document
from llama_index.core.schema import NodeRelationship
from llama_index.core.schema import TextNode

from services.chunking.common.errors import ChunkingFailed
from services.chunking.common.models import ChunkOptions
from services.chunking.token import TokenChunker
from services.chunking.token import _parser
from services.chunking.token import _tokenizer
from services.parsing.models import ParseRequest
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument


def test_plain_native_splitters_use_requested_sizes_and_overlap():
    tokenizer = _tokenizer()
    options = ChunkOptions(parent_chunk_size=100, child_chunk_size=30, chunk_overlap=8)
    parser = _parser(options, tokenizer)
    assert type(parser) is HierarchicalNodeParser
    parent = parser.node_parser_map["parent"]
    child = parser.node_parser_map["child"]
    assert type(parent) is type(child) is SentenceSplitter
    assert (parent.chunk_size, parent.chunk_overlap) == (100, 0)
    assert (child.chunk_size, child.chunk_overlap) == (30, 8)
    text = "A sentence has several words. " * 50
    nodes = parser.get_nodes_from_documents([Document(text=text)])
    roots = [node for node in nodes if node.parent_node is None]
    assert [node.text for node in roots] == parent.split_text(text)
    by_id = {node.node_id: node for node in nodes}
    for root in roots:
        children = [by_id[relation.node_id] for relation in root.child_nodes]
        assert [node.text for node in children] == child.split_text(root.text)


def test_business_mapping_uses_framework_relations_instead_of_reimplementing_split(
    monkeypatch,
):
    calls = []
    parent = TextNode(text="framework parent", id_="native-parent")
    child = TextNode(text="framework child", id_="native-child")
    parent.relationships[NodeRelationship.CHILD] = [child.as_related_node_info()]
    child.relationships[NodeRelationship.PARENT] = parent.as_related_node_info()

    def parse(self, documents, **kwargs):
        calls.append([document.text for document in documents])
        return [parent, child]

    monkeypatch.setattr(HierarchicalNodeParser, "get_nodes_from_documents", parse)
    parsed = ParsedDocument(
        ParseRequest("doc", 2, "source", "md", "guide.md", 1),
        "Guide",
        [ParsedBlock("text", "original text", page=9)],
    )
    chunks = TokenChunker().split(parsed, ChunkOptions())
    assert calls == [["original text"]]
    assert [chunk.content for chunk in chunks] == [
        "framework parent",
        "framework child",
    ]
    assert chunks[1].parent_id == chunks[0].id
    assert chunks[0].child_count == 1
    assert all(chunk.page_start == chunk.page_end == 9 for chunk in chunks)


def test_native_tokenizers_work_offline_and_accept_special_token_literals(monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("chunking must not download resources")

    monkeypatch.setattr(requests, "get", forbid_network)
    monkeypatch.setattr(nltk, "download", forbid_network)
    tokenizer = _tokenizer()
    parser = _parser(
        ChunkOptions(parent_chunk_size=60, child_chunk_size=20, chunk_overlap=4),
        tokenizer,
    )
    nodes = parser.get_nodes_from_documents(
        [Document(text=("Hello world. <|endoftext|> 中文🙂 " * 100))]
    )
    assert nodes
    assert tokenizer("hello world") == [15339, 1917]
    assert all(
        len(tokenizer(node.text)) <= (20 if node.parent_node else 60) for node in nodes
    )


def test_native_splitter_errors_become_business_errors(monkeypatch):
    def fail(self, documents, **kwargs):
        raise ValueError("Single token exceeded chunk size")

    monkeypatch.setattr(HierarchicalNodeParser, "get_nodes_from_documents", fail)
    parsed = ParsedDocument(
        ParseRequest("doc", 2, "source", "md", "guide.md", 1),
        "Guide",
        [ParsedBlock("text", "content")],
    )
    with pytest.raises(ChunkingFailed, match="LlamaIndex"):
        TokenChunker().split(parsed, ChunkOptions())
