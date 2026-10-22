# -*- coding: utf-8 -*-
"""文档解析服务入口。

当前只定义服务边界和依赖，不包含解析、切分、落库实现。
"""

from collections.abc import Mapping

from services.common.exceptions import FeatureNotReady
from services.parsing.models import ChunkOptions
from services.parsing.models import DocumentChunk
from services.parsing.models import ParseRequest
from services.parsing.models import ParseResult
from services.parsing.models import ParsedDocument
from services.parsing.ports import ChunkStore
from services.parsing.ports import DocumentChunker
from services.parsing.ports import DocumentParser
from services.parsing.ports import DocumentStore
from services.parsing.ports import ObjectReader
from services.parsing.ports import TaskStore


class DocumentParsingService:
    """解析任务的编排服务。

    后续实现顺序：

    1. 从 TaskStore 领取任务；
    2. 读取 Document 和 S3 对象；
    3. 选择 DocumentParser；
    4. 解析成 ParsedDocument；
    5. 用 DocumentChunker 生成父子块；
    6. 通过 ChunkStore 替换版本切分结果；
    7. 更新 documents 和 document_tasks 状态。
    """

    def __init__(
        self,
        *,
        objects: ObjectReader,
        documents: DocumentStore,
        tasks: TaskStore,
        chunks: ChunkStore,
        parsers: Mapping[str, DocumentParser],
        chunker: DocumentChunker,
        chunk_options: ChunkOptions | None = None,
    ) -> None:
        self._objects = objects
        self._documents = documents
        self._tasks = tasks
        self._chunks = chunks
        self._parsers = parsers
        self._chunker = chunker
        self._chunk_options = chunk_options or ChunkOptions()

    def process_task(self, task_id: str) -> ParseResult:
        """执行一条解析任务。"""
        raise FeatureNotReady("文档解析服务尚未实现：process_task")

    def parse(self, request: ParseRequest) -> ParsedDocument:
        """按文件类型选择解析器并解析源文件。"""
        raise FeatureNotReady("文档解析服务尚未实现：parse")

    def split(
        self,
        parsed: ParsedDocument,
        options: ChunkOptions | None = None,
    ) -> list[DocumentChunk]:
        """把解析结果切成父子块。"""
        raise FeatureNotReady("文档解析服务尚未实现：split")
