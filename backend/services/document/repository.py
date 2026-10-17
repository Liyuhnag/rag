# -*- coding: utf-8 -*-
"""测试用的进程内文档记录。正式环境走 model.document.orm 的 SQL。"""

from model.document import Document


class MemoryDocumentRepository:
    """测试用的进程内记录。"""

    def __init__(self) -> None:
        self.records: dict[str, Document] = {}

    def insert(self, document: Document) -> None:
        self.records[document.id] = document

    def get(self, document_id: str) -> Document | None:
        return self.records.get(document_id)

    def delete(self, document_id: str) -> Document | None:
        return self.records.pop(document_id, None)

    def list_page(
        self,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[Document], int]:
        records = list(self.records.values())
        reverse = sort_order != "asc"

        def sort_key(record: Document) -> tuple[object, str]:
            if sort_by == "name":
                value: object = record.name
            elif sort_by == "size_bytes":
                value = record.size_bytes
            else:
                value = record.uploaded_at
            return (value, record.id)

        records.sort(key=sort_key, reverse=reverse)
        start = (page - 1) * page_size
        return records[start : start + page_size], len(records)
