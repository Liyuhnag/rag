# -*- coding: utf-8 -*-
"""把文档处理提交到 Celery。"""

from task.document import process_document


def submit(task_id: str) -> None:
    """提交文档处理任务。"""
    process_document.delay(task_id)
