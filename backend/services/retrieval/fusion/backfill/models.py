# -*- coding: utf-8 -*-
"""父块三级回查（L1 本地缓存 -> L2 Redis -> L3 MySQL）数据模型。"""

from dataclasses import dataclass

from services.retrieval.fusion.models import MatchedChild


@dataclass(frozen=True)
class ParentChunkPayload:
    """缓存在 L1 / L2 及从 L3 MySQL 查询出的父块核心数据。"""

    parent_id: str
    document_id: str
    document_name: str
    version: int
    parent_index: int
    file_type: str
    content: str
    heading_path: str
    locator: str
    page_start: int | None
    page_end: int | None
    token_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "parent_id": self.parent_id,
            "document_id": self.document_id,
            "document_name": self.document_name,
            "version": self.version,
            "parent_index": self.parent_index,
            "file_type": self.file_type,
            "content": self.content,
            "heading_path": self.heading_path,
            "locator": self.locator,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "token_count": self.token_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ParentChunkPayload":
        page_start = data.get("page_start")
        page_end = data.get("page_end")
        return cls(
            parent_id=str(data["parent_id"]),
            document_id=str(data["document_id"]),
            document_name=str(data.get("document_name", "")),
            version=int(data.get("version", 1)),
            parent_index=int(data.get("parent_index") or 0),
            file_type=str(data.get("file_type", "")),
            content=str(data.get("content", "")),
            heading_path=str(data.get("heading_path", "")),
            locator=str(data.get("locator", "")),
            page_start=None if page_start is None else int(page_start),
            page_end=None if page_end is None else int(page_end),
            token_count=int(data.get("token_count") or 0),
        )


@dataclass(frozen=True)
class FusedParent:
    """回查父块正文后组装完成的最终父块对象（可直接返回或送入 Rerank）。"""

    parent_id: str
    document_id: str
    document_name: str
    version: int
    parent_index: int
    file_type: str
    content: str
    heading_path: str
    locator: str
    page_start: int | None
    page_end: int | None
    token_count: int
    score: float
    rrf_score: float
    rerank_score: float | None
    rank: int
    matched_child_count: int
    matched_children: tuple[MatchedChild, ...]


@dataclass(frozen=True)
class BackfillResult:
    """父块三级回查输出结果与各级命中统计。"""

    parents: tuple[FusedParent, ...] = ()
    missing_parent_count: int = 0
    l1_hits: int = 0
    l2_hits: int = 0
    l3_hits: int = 0
    backfill_ms: int = 0
