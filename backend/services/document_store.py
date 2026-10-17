# -*- coding: utf-8 -*-
"""文档服务占位。存储、分块和向量化由后续实现补上。"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from services.exceptions import FeatureNotReady


@dataclass
class DocumentRecord:
    """服务返回给 API 的文档记录。"""

    id: str
    name: str
    file_type: str
    size_bytes: int
    chunk_count: int
    status: str
    operator: str
    uploaded_at: datetime
    stored_name: str


class DocumentStore:
    """文档服务占位。"""

    def __init__(self, upload_dir: Path) -> None:
        self.upload_dir = upload_dir

    def add(
        self,
        name: str,
        file_type: str,
        content: bytes,
        operator: str,
    ) -> DocumentRecord:
        del name, file_type, content, operator
        raise FeatureNotReady("文档服务尚未接入")

    def get(self, document_id: str) -> DocumentRecord | None:
        del document_id
        raise FeatureNotReady("文档服务尚未接入")

    def delete(self, document_id: str) -> None:
        del document_id
        raise FeatureNotReady("文档服务尚未接入")

    def list_page(
        self,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[DocumentRecord], int]:
        del page, page_size, sort_by, sort_order
        raise FeatureNotReady("文档服务尚未接入")
