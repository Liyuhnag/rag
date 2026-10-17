# -*- coding: utf-8 -*-
"""提交异步任务。测试里改为当场执行。"""

from threading import Thread

from task.document import process_document


_inline = False


def use_inline(enabled: bool = True) -> None:
    """测试时在请求里把任务跑完。"""
    global _inline
    _inline = enabled


def submit(task_id: str) -> None:
    """提交文档处理任务。"""
    if _inline:
        process_document(task_id)
        return
    Thread(target=process_document, args=(task_id,), daemon=True).start()
