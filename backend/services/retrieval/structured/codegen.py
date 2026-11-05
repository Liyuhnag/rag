# -*- coding: utf-8 -*-
"""让模型根据问题和表样例生成一段只读的 pandas 计算代码。"""

import json
from collections.abc import Sequence

import httpx

from config import Settings
from model.structured_dataset.model import StructuredDataset
from services.retrieval.structured.models import DataTable

_SYSTEM_PROMPT = """你是企业表格分析的代码生成器。根据用户问题和给出的表样例，编写 Python 代码完成计算。

可用变量：
- tables：dict，键是工作表名，值是 pandas.DataFrame，数据已经装入，不要再读取文件
- pd：pandas

要求：
1. 只输出 Python 代码，不要 Markdown，不要解释
2. 把最终答案赋给 result
3. result 只能是数字、字符串、布尔值、列表，或行数不多的 DataFrame
4. 禁止 import，禁止读写文件、数据库和网络
5. 工作表名和字段名必须使用输入里出现的原名
"""


class DashScopeTableCodeWriter:
    """调用 DashScope 对话接口生成表格计算代码。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str | None = None,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-plus",
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if settings is not None:
            api_key = settings.dashscope_api_key
            base_url = settings.embedding_base_url
            model = settings.router_model
        self.api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def write(
        self,
        query: str,
        datasets: Sequence[StructuredDataset],
        tables: Sequence[DataTable],
    ) -> str:
        """返回可交给代码执行器的 Python 代码。"""
        if not self.api_key:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY")
        with httpx.Client(
            transport=self.transport,
            timeout=httpx.Timeout(self.timeout_seconds, connect=3.0),
        ) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0.0,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(
                                _prompt_payload(query, datasets, tables),
                                ensure_ascii=False,
                            ),
                        },
                    ],
                },
            )
        if not response.is_success:
            raise RuntimeError(f"表格代码生成失败（HTTP {response.status_code}）")
        return _parse_code(response.json())


def _prompt_payload(
    query: str,
    datasets: Sequence[StructuredDataset],
    tables: Sequence[DataTable],
) -> dict[str, object]:
    schemas = {dataset.sheet_name: list(dataset.schema_json) for dataset in datasets}
    described = []
    for table in tables:
        described.append(
            {
                "name": table.name,
                "columns": schemas.get(table.name, []),
                "sample_rows": [dict(row) for row in table.rows[:3]],
            }
        )
    return {"query": query, "tables": described}


def _parse_code(payload: dict[str, object]) -> str:
    try:
        choices = payload["choices"]
        content = choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("表格代码生成结果无法解析") from exc
    if not isinstance(content, str):
        raise RuntimeError("表格代码生成结果无法解析")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text:
        raise RuntimeError("表格代码生成结果为空")
    return text
