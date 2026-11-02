# -*- coding: utf-8 -*-
"""结构化查询边界。

表结构目录已经可以定位数据集，数据行还没有写入可查询的表，这里不生成也不执行 SQL。
"""

from services.retrieval.structured.models import StructuredQueryRequest
from services.retrieval.structured.models import StructuredQueryResult


class StructuredQueryService:
    """接收路由选中的数据集。查询执行留到数据入库之后。"""

    def query(self, request: StructuredQueryRequest) -> StructuredQueryResult:
        """返回命中的数据集，并说明当前不能执行查询。"""
        dataset_ids = tuple(dataset.id for dataset in request.datasets)
        if not dataset_ids:
            return StructuredQueryResult(message="没有可查询的结构化数据集")
        return StructuredQueryResult(
            dataset_ids=dataset_ids,
            message="结构化数据尚未入库，不能执行查询",
        )
