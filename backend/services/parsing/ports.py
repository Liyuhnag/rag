# -*- coding: utf-8 -*-
"""文档解析服务依赖的端口定义。

这里只声明服务需要什么，不绑定 MySQL、S3、MinerU 或具体解析器。
实现方在 worker 启动时注入。
"""

from datetime import datetime
from typing import Protocol

from model.document.model import Document
from model.document_chunk.model import DocumentChunk
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from services.parsing.models import ChunkOptions
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument


class ObjectReader(Protocol):
    """读取和写入对象存储。"""

    def read_object(self, key: str) -> bytes:
        """读取完整对象。"""
        ...

    def write_object(self, key: str, content: bytes, content_type: str) -> None:
        """写入解析产物。"""
        ...


class DocumentStore(Protocol):
    """读取文档并更新文档状态。"""

    def get(self, document_id: str) -> Document | None:
        ...

    def begin_parse(
        self,
        document_id: str,
        parse_version: int,
        now: datetime,
    ) -> None:
        ...

    def mark_ready(
        self,
        document_id: str,
        chunk_count: int,
        parent_chunk_count: int,
        now: datetime,
    ) -> None:
        ...

    def mark_failed(
        self,
        document_id: str,
        message: str,
        now: datetime,
    ) -> None:
        ...


class TaskStore(Protocol):
    """领取任务并更新任务状态。"""

    def claim(self, task_id: str, now: datetime) -> DocumentTask | None:
        ...

    def update_progress(
        self,
        task_id: str,
        stage: DocumentTaskStage,
        progress: int,
        step: str,
        now: datetime,
    ) -> None:
        ...

    def complete(self, task_id: str, step: str, now: datetime) -> None:
        ...

    def fail(self, task_id: str, message: str, now: datetime) -> None:
        ...


class ChunkStore(Protocol):
    """替换某个文档版本的切分结果。"""

    def replace_for_version(
        self,
        document_id: str,
        version: int,
        chunks: list[DocumentChunk],
    ) -> None:
        ...


class DocumentParser(Protocol):
    """单一文件类型的解析器。"""

    def supports(self, file_type: str) -> bool:
        ...

    def parse(self, source: ParseInput) -> ParsedDocument:
        ...


class DocumentChunker(Protocol):
    """把解析结果切成父子块。"""

    def split(
        self,
        parsed: ParsedDocument,
        options: ChunkOptions,
    ) -> list[DocumentChunk]:
        ...
