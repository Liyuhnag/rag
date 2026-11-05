# -*- coding: utf-8 -*-
"""子进程入口：执行一段表格计算代码，并把 JSON 结果写到标准输出。"""

import io
import json
import math
import sys
from contextlib import redirect_stdout

import pandas as pd

_MAX_RESULT_ROWS = 100
_MAX_STDOUT_CHARS = 2000
_BLOCKED_READS = (
    "read_clipboard",
    "read_csv",
    "read_excel",
    "read_feather",
    "read_html",
    "read_json",
    "read_orc",
    "read_parquet",
    "read_pickle",
    "read_sas",
    "read_spss",
    "read_sql",
    "read_sql_query",
    "read_sql_table",
    "read_stata",
    "read_xml",
)
_BLOCKED_WRITES = (
    "to_clipboard",
    "to_csv",
    "to_excel",
    "to_feather",
    "to_html",
    "to_json",
    "to_parquet",
    "to_pickle",
    "to_sql",
    "to_stata",
    "to_xml",
)
_BUILTIN_NAMES = (
    "False",
    "None",
    "True",
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "float",
    "int",
    "isinstance",
    "len",
    "list",
    "max",
    "min",
    "print",
    "range",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
)


def _blocked(*_args, **_kwargs) -> None:
    raise PermissionError("代码执行器禁止读写文件或数据库")


def _safe_builtins() -> dict[str, object]:
    source = __builtins__
    if not isinstance(source, dict):
        source = vars(source)
    return {name: source[name] for name in _BUILTIN_NAMES if name in source}


def _lock_pandas() -> None:
    for name in _BLOCKED_READS:
        if hasattr(pd, name):
            setattr(pd, name, _blocked)
    for owner in (pd.DataFrame, pd.Series):
        for name in _BLOCKED_WRITES:
            if hasattr(owner, name):
                setattr(owner, name, _blocked)


def _jsonable(value: object, truncated: bool) -> tuple[object, bool]:
    if value is None or isinstance(value, (bool, str)):
        return value, truncated
    if isinstance(value, int):
        return int(value), truncated
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("计算结果不是有限数字")
        return value, truncated
    if isinstance(value, pd.DataFrame):
        preview = value.head(_MAX_RESULT_ROWS)
        rows = []
        overflow = len(value) > _MAX_RESULT_ROWS
        for row in preview.to_dict(orient="records"):
            converted, truncated = _jsonable(row, truncated)
            rows.append(converted)
        return {
            "columns": [str(column) for column in value.columns],
            "rows": rows,
            "row_count": int(len(value)),
            "truncated": overflow,
        }, truncated or overflow
    if isinstance(value, pd.Series):
        frame = value.to_frame(name=value.name or "value")
        return _jsonable(frame, truncated)
    if isinstance(value, pd.Timestamp):
        return value.isoformat(), truncated
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _jsonable(item(), truncated)
        except (TypeError, ValueError):
            pass
    if isinstance(value, (list, tuple)):
        items = []
        overflow = len(value) > _MAX_RESULT_ROWS
        for item_value in list(value)[:_MAX_RESULT_ROWS]:
            converted, truncated = _jsonable(item_value, truncated)
            items.append(converted)
        return items, truncated or overflow
    if isinstance(value, dict):
        converted: dict[str, object] = {}
        for key, item_value in value.items():
            converted[str(key)], truncated = _jsonable(item_value, truncated)
        return converted, truncated
    raise ValueError("计算结果无法序列化")


def run(payload: dict[str, object]) -> dict[str, object]:
    """在当前进程执行代码。仅供子进程入口调用。"""
    code = payload.get("code")
    tables = payload.get("tables")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("计算代码为空")
    if not isinstance(tables, dict) or not tables:
        raise ValueError("没有可计算的表格")
    _lock_pandas()
    frames: dict[str, pd.DataFrame] = {}
    for name, rows in tables.items():
        if not isinstance(name, str) or not isinstance(rows, list):
            raise ValueError("表格数据格式无效")
        frames[name] = pd.DataFrame(rows)
    stdout = io.StringIO()
    namespace: dict[str, object] = {
        "__builtins__": _safe_builtins(),
        "pd": pd,
        "tables": frames,
    }
    with redirect_stdout(stdout):
        exec(code, namespace)
    if "result" not in namespace:
        raise ValueError("计算代码没有设置 result")
    value, truncated = _jsonable(namespace["result"], False)
    text = stdout.getvalue()
    if len(text) > _MAX_STDOUT_CHARS:
        text = text[:_MAX_STDOUT_CHARS]
    return {
        "ok": True,
        "value": value,
        "truncated": truncated,
        "stdout": text,
        "error": None,
    }


def main() -> None:
    """读取标准输入中的代码和表格，输出一条 JSON。"""
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("执行请求格式无效")
        output = run(payload)
    except Exception as exc:
        # 子进程是执行边界，任何失败都变成 JSON，避免父进程只看到崩溃。
        output = {
            "ok": False,
            "value": None,
            "truncated": False,
            "stdout": "",
            "error": str(exc),
        }
    sys.stdout.write(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
