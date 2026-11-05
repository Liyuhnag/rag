# -*- coding: utf-8 -*-
"""结构化查询的输入和输出。"""

from dataclasses import dataclass

from model.structured_dataset.model import StructuredDataset


@dataclass(frozen=True)
class DataTable:
    """装入代码执行器的一张工作表。"""

    name: str
    rows: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class StructuredQueryRequest:
    """已经路由到表查询的请求。"""

    query: str
    datasets: tuple[StructuredDataset, ...] = ()
    request_id: str = ""


@dataclass(frozen=True)
class StructuredQueryResult:
    """表查询结果。没有数据行时 ready 为 false；计算完成后 value 保存结果。"""

    dataset_ids: tuple[str, ...] = ()
    ready: bool = False
    message: str = ""
    value: object = None
    error: str | None = None
