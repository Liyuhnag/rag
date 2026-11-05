# -*- coding: utf-8 -*-
"""表格代码执行器。

模型生成的代码在独立子进程里运行，只拿到已装入的 DataFrame。
进程不继承接口密钥，并限制读写文件的常见 pandas 入口。这不是对抗恶意代码的完整沙箱。
"""

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from services.retrieval.structured.models import DataTable

_RUNNER = Path(__file__).with_name("runner.py")
_DEFAULT_TIMEOUT_SECONDS = 5.0
_DEFAULT_MAX_CODE_CHARS = 8000
_DEFAULT_MAX_ROWS = 50000
_SECRET_SUFFIXES = ("_API_KEY", "_PASSWORD", "_SECRET", "_TOKEN")
_SECRET_NAMES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "DASHSCOPE_API_KEY",
    "ES_PASSWORD",
    "MYSQL_PASSWORD",
    "S3_SECRET_ACCESS_KEY",
}


@dataclass(frozen=True)
class CodeExecutionResult:
    """一次表格计算的结果。失败时 value 为空，error 说明原因。"""

    ok: bool
    value: object = None
    truncated: bool = False
    stdout: str = ""
    error: str | None = None


class TableCodeExecutor:
    """用当前 Python 启动 runner，在超时内执行一段表格代码。"""

    def __init__(
        self,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_code_chars: int = _DEFAULT_MAX_CODE_CHARS,
        max_rows: int = _DEFAULT_MAX_ROWS,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_code_chars = max_code_chars
        self._max_rows = max_rows

    def execute(self, code: str, tables: Sequence[DataTable]) -> CodeExecutionResult:
        """执行代码。代码需要把答案赋给 result，tables 的键是工作表名。"""
        if len(code) > self._max_code_chars:
            return _failed("计算代码过长")
        payload_tables, error = _payload_tables(tables, self._max_rows)
        if error is not None:
            return _failed(error)
        try:
            with tempfile.TemporaryDirectory(prefix="rag-table-exec-") as workdir:
                completed = subprocess.run(
                    [sys.executable, str(_RUNNER)],
                    input=json.dumps(
                        {"code": code, "tables": payload_tables},
                        ensure_ascii=False,
                    ),
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    cwd=workdir,
                    env=_child_env(),
                    check=False,
                )
        except subprocess.TimeoutExpired:
            return _failed("表格计算超时")
        if not completed.stdout.strip():
            detail = completed.stderr.strip() or f"执行进程退出 {completed.returncode}"
            return _failed(detail[:500])
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return _failed("代码执行器返回了无法解析的结果")
        if not isinstance(payload, dict):
            return _failed("代码执行器返回了无法解析的结果")
        return CodeExecutionResult(
            ok=bool(payload.get("ok")),
            value=payload.get("value"),
            truncated=bool(payload.get("truncated")),
            stdout=str(payload.get("stdout") or ""),
            error=None if payload.get("error") is None else str(payload.get("error")),
        )


def _payload_tables(
    tables: Sequence[DataTable],
    max_rows: int,
) -> tuple[dict[str, list[dict[str, object]]], str | None]:
    payload: dict[str, list[dict[str, object]]] = {}
    total = 0
    for table in tables:
        if not table.name or table.name in payload:
            return {}, "表格名称无效或重复"
        total += len(table.rows)
        if total > max_rows:
            return {}, "表格行数超过执行上限"
        payload[table.name] = [dict(row) for row in table.rows]
    if not payload:
        return {}, "没有可计算的表格"
    return payload, None


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in list(env):
        if name in _SECRET_NAMES or name.endswith(_SECRET_SUFFIXES):
            env.pop(name, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _failed(error: str) -> CodeExecutionResult:
    return CodeExecutionResult(ok=False, error=error)
