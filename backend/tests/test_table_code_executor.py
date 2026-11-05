# -*- coding: utf-8 -*-
"""表格代码执行器：只计算传入的表，并挡住文件和超时。"""

import json

import httpx

from model.structured_dataset.model import StructuredDataset
from services.retrieval.structured.codegen import DashScopeTableCodeWriter
from services.retrieval.structured.executor import TableCodeExecutor
from services.retrieval.structured.models import DataTable
from services.retrieval.structured.models import StructuredQueryRequest
from services.retrieval.structured.service import StructuredQueryService


def _table() -> DataTable:
    return DataTable(
        name="销售明细",
        rows=({"区域": "华东", "金额": 10}, {"区域": "华南", "金额": 20}),
    )


def _dataset() -> StructuredDataset:
    return StructuredDataset(
        id="ds-1",
        document_id="doc-xlsx",
        version=1,
        sheet_name="销售明细",
        file_type="xlsx",
        schema_json=[
            {"name": "区域", "type": "string", "nullable": False},
            {"name": "金额", "type": "integer", "nullable": False},
        ],
        row_count=2,
        source_json_key="parsed.json",
        source_md_key="parsed.md",
    )


def test_executor_sums_a_loaded_table():
    result = TableCodeExecutor().execute(
        'result = int(tables["销售明细"]["金额"].sum())',
        (_table(),),
    )
    assert result.ok
    assert result.value == 30
    assert result.error is None


def test_executor_returns_a_truncated_table_preview():
    rows = tuple({"金额": index} for index in range(120))
    result = TableCodeExecutor().execute(
        'result = tables["销售明细"]',
        (DataTable("销售明细", rows),),
    )
    assert result.ok
    assert result.truncated
    assert result.value["row_count"] == 120
    assert len(result.value["rows"]) == 100


def test_executor_rejects_import_and_file_reads():
    executor = TableCodeExecutor()
    imported = executor.execute("import os\nresult = 1", (_table(),))
    assert not imported.ok
    assert imported.error

    read_file = executor.execute(
        'result = pd.read_csv("/etc/passwd")',
        (_table(),),
    )
    assert not read_file.ok
    assert "禁止读写文件" in (read_file.error or "")


def test_executor_times_out_instead_of_hanging():
    result = TableCodeExecutor(timeout_seconds=0.5).execute(
        "while True:\n    pass",
        (_table(),),
    )
    assert not result.ok
    assert result.error == "表格计算超时"


def test_code_writer_strips_markdown_and_sends_table_sample():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '```python\nresult = int(tables["销售明细"]["金额"].sum())\n```'
                        }
                    }
                ]
            },
        )

    writer = DashScopeTableCodeWriter(
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )
    code = writer.write("金额合计是多少", (_dataset(),), (_table(),))
    assert code == 'result = int(tables["销售明细"]["金额"].sum())'
    user_payload = json.loads(captured["body"]["messages"][1]["content"])
    assert user_payload["tables"][0]["name"] == "销售明细"
    assert user_payload["tables"][0]["sample_rows"][0]["区域"] == "华东"


class _Loader:
    def load(self, datasets):
        assert datasets[0].id == "ds-1"
        return (_table(),)


class _Writer:
    def write(self, query, datasets, tables):
        assert query == "金额合计"
        assert tables[0].name == "销售明细"
        return 'result = int(tables["销售明细"]["金额"].sum())'


def test_structured_query_executes_generated_code_when_rows_exist():
    service = StructuredQueryService(loader=_Loader(), writer=_Writer())
    result = service.query(
        StructuredQueryRequest(query="金额合计", datasets=(_dataset(),), request_id="req-1")
    )
    assert result.ready
    assert result.value == 30
    assert result.message == "30"
    assert result.dataset_ids == ("ds-1",)
    assert result.error is None
