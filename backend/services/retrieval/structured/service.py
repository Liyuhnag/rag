# -*- coding: utf-8 -*-
"""结构化查询：有数据行时生成 pandas 代码并交给执行器，否则只返回数据集。"""

import logging
from collections.abc import Sequence

from services.retrieval.structured.executor import TableCodeExecutor
from services.retrieval.structured.models import DataTable
from services.retrieval.structured.models import StructuredQueryRequest
from services.retrieval.structured.models import StructuredQueryResult

logger = logging.getLogger("rag.retrieval.structured")


class TableLoader:
    """按数据集读取已经解析出的行。返回空表示数据还不可计算。"""

    def load(self, datasets: Sequence[object]) -> tuple[DataTable, ...]:
        del datasets
        return ()


class TableCodeWriter:
    """根据问题和表样例生成计算代码。"""

    def write(self, query: str, datasets: Sequence[object], tables: Sequence[DataTable]) -> str:
        del query, datasets, tables
        raise RuntimeError("未配置表格代码生成")


class StructuredQueryService:
    """接收路由选中的数据集。行数据由 loader 提供，计算交给代码执行器。"""

    def __init__(
        self,
        loader: TableLoader | None = None,
        writer: TableCodeWriter | None = None,
        executor: TableCodeExecutor | None = None,
    ) -> None:
        self._loader = loader
        self._writer = writer
        self._executor = executor or TableCodeExecutor()

    def query(self, request: StructuredQueryRequest) -> StructuredQueryResult:
        """有表数据时执行计算；没有数据行时保持原来的未入库结果。"""
        dataset_ids = tuple(dataset.id for dataset in request.datasets)
        if not dataset_ids:
            return StructuredQueryResult(message="没有可查询的结构化数据集")
        tables = () if self._loader is None else self._loader.load(request.datasets)
        if not tables:
            return StructuredQueryResult(
                dataset_ids=dataset_ids,
                message="结构化数据尚未入库，不能执行查询",
            )
        if self._writer is None:
            return StructuredQueryResult(
                dataset_ids=dataset_ids,
                message="未配置表格代码生成",
            )
        try:
            code = self._writer.write(request.query, request.datasets, tables)
        except Exception as exc:
            # 代码生成失败只影响表查询，文档检索结果仍然可以返回。
            logger.warning(
                "表格代码生成失败 request_id=%s error=%s",
                request.request_id,
                type(exc).__name__,
            )
            return StructuredQueryResult(
                dataset_ids=dataset_ids,
                message="表格代码生成失败",
            )
        execution = self._executor.execute(code, tables)
        if not execution.ok:
            logger.warning(
                "表格计算失败 request_id=%s error=%s",
                request.request_id,
                execution.error or "unknown",
            )
            return StructuredQueryResult(
                dataset_ids=dataset_ids,
                ready=True,
                message="表格计算失败",
                error=execution.error,
            )
        return StructuredQueryResult(
            dataset_ids=dataset_ids,
            ready=True,
            message=_message(execution.value),
            value=execution.value,
        )


def _message(value: object) -> str:
    if isinstance(value, dict) and "row_count" in value:
        return f"返回 {value['row_count']} 行"
    text = str(value)
    if len(text) > 500:
        return text[:500]
    return text
