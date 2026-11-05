# -*- coding: utf-8 -*-
"""导入结构回归测试。"""

import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_importing_tasks_does_not_import_main() -> None:
    """任务模块不能反向导入 main。

    否则 reload 的 spawn 子进程重建 __main__ 时，main 会在
    api 包半初始化的状态下被二次导入，导致路由对象绑定成模块。
    """
    code = (
        "import sys; import task; "
        "assert 'main' not in sys.modules, 'task 不应导入 main'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
