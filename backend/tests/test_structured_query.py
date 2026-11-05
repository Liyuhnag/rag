# -*- coding: utf-8 -*-
"""结构化查询在没有数据行时只返回数据集。"""

from model.structured_dataset.model import StructuredDataset
from services.retrieval.structured.models import StructuredQueryRequest
from services.retrieval.structured.service import StructuredQueryService


def _dataset() -> StructuredDataset:
    return StructuredDataset(
        id="ds-1",
        document_id="doc-xlsx",
        version=2,
        sheet_name="销售明细",
        file_type="xlsx",
        schema_json=[{"name": "金额", "type": "number", "nullable": False}],
        row_count=10,
        source_json_key="parsed.json",
        source_md_key="parsed.md",
    )


def test_structured_query_reports_rows_are_not_loaded():
    result = StructuredQueryService().query(
        StructuredQueryRequest(query="华东区收入合计", datasets=(_dataset(),))
    )
    assert result.dataset_ids == ("ds-1",)
    assert not result.ready
    assert result.message == "结构化数据尚未入库，不能执行查询"


def test_structured_query_without_datasets_stays_empty():
    result = StructuredQueryService().query(StructuredQueryRequest(query="合计"))
    assert result.dataset_ids == ()
    assert result.message == "没有可查询的结构化数据集"
