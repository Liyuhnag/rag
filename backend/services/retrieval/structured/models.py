# -*- coding: utf-8 -*-
"""结构化查询的输入和输出。"""

from dataclasses import dataclass

from model.structured_dataset.model import StructuredDataset


@dataclass(frozen=True)
class StructuredQueryRequest:
    """已经路由到表查询的请求。"""

    query: str
    datasets: tuple[StructuredDataset, ...] = ()
    request_id: str = ""


@dataclass(frozen=True)
class StructuredQueryResult:
    """表查询结果。当前数据行尚未入库，ready 保持为 false。"""

    dataset_ids: tuple[str, ...] = ()
    ready: bool = False
    message: str = ""
