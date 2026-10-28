# -*- coding: utf-8 -*-
"""Map native LlamaIndex parent/child nodes to versioned business chunks."""

from collections.abc import Iterator
from functools import partial
from uuid import NAMESPACE_URL
from uuid import uuid5

import tiktoken
from llama_index.core.node_parser import HierarchicalNodeParser
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.node_parser.text.utils import split_by_sentence_tokenizer_internal
from llama_index.core.schema import Document
from nltk.tokenize import PunktSentenceTokenizer

from model.document_chunk.model import ChunkLevel
from model.document_chunk.model import DocumentChunk
from services.chunking.common.errors import ChunkingFailed
from services.chunking.common.models import ChunkOptions
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument


def _tokenizer():
    """Use tiktoken's native encoding initialization and cache handling."""
    return tiktoken.get_encoding("cl100k_base").encode_ordinary


def _groups(
    parsed: ParsedDocument, size: int, tokenizer
) -> Iterator[tuple[ParsedBlock, str, str]]:
    """Batch compatible blocks; never collect a document or split text ourselves."""
    first = None
    text = ""
    locators: list[str] = []
    count = 0
    for block in parsed.blocks:
        content = block.text
        if not content.strip():
            continue
        locator = block.locator
        if block.sheet:
            locator = f"sheet:{block.sheet}" + (f"/{locator}" if locator else "")
        if len(locator) > 512:
            raise ChunkingFailed("Source locator exceeds 512 characters")
        separator = "" if block.metadata.get("verbatim") else "\n\n"
        candidate = text + separator + content if text else content
        candidate_locators = list(locators)
        if locator and locator not in candidate_locators:
            candidate_locators.append(locator)
        compatible = first is None or (
            first.page == block.page
            and first.sheet == block.sheet
            and first.heading_path == block.heading_path
            and first.kind == block.kind
        )
        if first is not None and (
            not compatible
            or count >= 128
            or len(candidate) > 65536
            or len("; ".join(candidate_locators)) > 512
            or len(tokenizer(candidate)) > size
        ):
            yield first, text, "; ".join(locators)
            first = None
            count = 0
        if first is None:
            first = block
            text = content
            locators = [locator] if locator else []
        else:
            text = candidate
            locators = candidate_locators
        count += 1
    if first is not None:
        yield first, text, "; ".join(locators)


def _parser(options: ChunkOptions, tokenizer) -> HierarchicalNodeParser:
    # LlamaIndex's default helper downloads NLTK data before constructing this
    # same resource-free native tokenizer. Use its sentence-span adapter directly.
    sentences = partial(
        split_by_sentence_tokenizer_internal, tokenizer=PunktSentenceTokenizer()
    )
    splitters = {}
    for name, size, overlap in (
        ("parent", options.parent_chunk_size, 0),
        ("child", options.child_chunk_size, options.chunk_overlap),
    ):
        splitters[name] = SentenceSplitter(
            chunk_size=size,
            chunk_overlap=overlap,
            tokenizer=tokenizer,
            chunking_tokenizer_fn=sentences,
            include_metadata=False,
            include_prev_next_rel=False,
        )
    return HierarchicalNodeParser.from_defaults(
        node_parser_ids=["parent", "child"],
        node_parser_map=splitters,
        include_metadata=False,
        include_prev_next_rel=False,
    )


class TokenChunker:
    """Run a native two-level parser on one bounded source group at a time."""

    def split(
        self, parsed: ParsedDocument, options: ChunkOptions
    ) -> list[DocumentChunk]:
        return list(self.iter_split(parsed, options))

    def iter_split(
        self, parsed: ParsedDocument, options: ChunkOptions
    ) -> Iterator[DocumentChunk]:
        """Yield parents before children; provenance describes the source group."""
        tokenizer = _tokenizer()
        parser = _parser(options, tokenizer)
        chunk_index = 0
        parent_index = 0
        for block, text, locator in _groups(
            parsed, options.parent_chunk_size, tokenizer
        ):
            try:
                nodes = parser.get_nodes_from_documents([Document(text=text)])
            except ValueError as exc:
                raise ChunkingFailed(
                    f"LlamaIndex could not split source block: {exc}"
                ) from exc
            by_id = {node.node_id: node for node in nodes}
            for node in nodes:
                if node.parent_node is not None:
                    continue
                parent_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"rag:{parsed.request.document_id}:{parsed.request.version}:parent:{parent_index}",
                    )
                )
                children = [by_id[child.node_id] for child in node.child_nodes or []]
                for order, current in enumerate([node, *children]):
                    is_parent = order == 0
                    content = current.get_content()
                    yield DocumentChunk(
                        id=parent_id
                        if is_parent
                        else str(
                            uuid5(NAMESPACE_URL, f"{parent_id}:child:{order - 1}")
                        ),
                        document_id=parsed.request.document_id,
                        version=parsed.request.version,
                        chunk_level=ChunkLevel.PARENT
                        if is_parent
                        else ChunkLevel.CHILD,
                        parent_id=None if is_parent else parent_id,
                        parent_index=parent_index,
                        child_order=None if is_parent else order - 1,
                        chunk_index=chunk_index,
                        child_count=len(children) if is_parent else 0,
                        file_type=parsed.request.file_type,
                        page_start=block.page,
                        page_end=block.page,
                        locator=locator,
                        heading_path=block.heading_path,
                        content=content,
                        token_count=len(tokenizer(content)),
                    )
                    chunk_index += 1
                parent_index += 1
