# -*- coding: utf-8 -*-
"""把文档处理提交到 Celery。测试里改为当场执行。"""

from task.celery_app import celery_app
from task.document import process_document


def use_inline(enabled: bool = True) -> None:
    """测试时在请求里把任务跑完，不连接 Redis。"""
    celery_app.conf.task_always_eager = enabled
    celery_app.conf.task_eager_propagates = enabled


def submit(task_id: str) -> None:
    """提交文档处理任务。"""
    process_document.delay(task_id)
