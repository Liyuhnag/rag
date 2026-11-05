# -*- coding: utf-8 -*-
"""将 LlamaIndex 原生父子节点映射为带版本标识的业务切分块。"""

from collections.abc import Callable
from collections.abc import Iterator
from functools import partial
from uuid import NAMESPACE_URL
from uuid import uuid5

import tiktoken
from llama_index.core.node_parser import HierarchicalNodeParser
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.node_parser import get_child_nodes
from llama_index.core.node_parser import get_root_nodes
from llama_index.core.node_parser.text.utils import split_by_sentence_tokenizer_internal
from llama_index.core.schema import Document
from nltk.tokenize import PunktSentenceTokenizer

from model.document_chunk.model import ChunkLevel
from model.document_chunk.model import DocumentChunk
from services.indexing.chunking.common.errors import ChunkingFailed
from services.indexing.chunking.common.models import ChunkOptions
from services.indexing.parsing.models import ParsedBlock
from services.indexing.parsing.models import ParsedDocument


def _groups(parsed: ParsedDocument) -> Iterator[tuple[ParsedBlock, str, str]]:
    """按页码、工作表与标题路径聚合相邻文本块，具体按 token 切分交由 LlamaIndex 处理。"""
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


class TokenChunker:
    """按有界分组逐段调用 LlamaIndex 原生双层切分器。"""

    def __init__(self, tokenizer: Callable[[str], list[int]] | None = None) -> None:
        self.tokenizer = (
            tokenizer or tiktoken.get_encoding("cl100k_base").encode_ordinary
        )
        # LlamaIndex 默认辅助函数会尝试联网下载 NLTK 数据包；
        # 这里直接持有无需外部资源的 PunktSentenceTokenizer 保持离线可用。
        self.sentence_tokenizer = partial(
            split_by_sentence_tokenizer_internal,
            tokenizer=PunktSentenceTokenizer(),
        )

    def build_parser(self, options: ChunkOptions) -> HierarchicalNodeParser:
        """根据切分参数构造父子双层切分器。"""
        splitters = {
            name: SentenceSplitter(
                chunk_size=size,
                chunk_overlap=overlap,
                tokenizer=self.tokenizer,
                chunking_tokenizer_fn=self.sentence_tokenizer,
                include_metadata=False,
                include_prev_next_rel=False,
            )
            for name, size, overlap in (
                ("parent", options.parent_chunk_size, 0),
                ("child", options.child_chunk_size, options.chunk_overlap),
            )
        }
        return HierarchicalNodeParser.from_defaults(
            node_parser_ids=["parent", "child"],
            node_parser_map=splitters,
            include_metadata=False,
            include_prev_next_rel=False,
        )

    def split(
        self, parsed: ParsedDocument, options: ChunkOptions
    ) -> list[DocumentChunk]:
        return list(self.iter_split(parsed, options))

    def iter_split(
        self, parsed: ParsedDocument, options: ChunkOptions
    ) -> Iterator[DocumentChunk]:
        """按「父块在前、所属子块在后」的顺序产出，并附带来源定位信息。"""
        parser = self.build_parser(options)
        chunk_index = 0
        parent_index = 0
        for block, text, locator in _groups(parsed):
            try:
                nodes = parser.get_nodes_from_documents([Document(text=text)])
            except ValueError as exc:
                raise ChunkingFailed(
                    f"LlamaIndex could not split source block: {exc}"
                ) from exc
            for root in get_root_nodes(nodes):
                parent_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"rag:{parsed.request.document_id}:{parsed.request.version}:parent:{parent_index}",
                    )
                )
                children = get_child_nodes([root], all_nodes=nodes)
                for order, current in enumerate([root, *children]):
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
                        token_count=len(self.tokenizer(content)),
                    )
                    chunk_index += 1
                parent_index += 1
