# -*- coding: utf-8 -*-
"""任务执行权失效的明确结果。"""


class LeaseLost(Exception):
    """当前 worker 不再持有该任务的执行权，必须停止写入。"""
