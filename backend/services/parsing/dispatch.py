# -*- coding: utf-8 -*-
"""把 document_tasks 投递到解析 Worker。"""

import logging

logger = logging.getLogger("rag.task")


def dispatch_task(task_id: str) -> bool:
    """投递解析任务；失败只记录日志，任务保持 PENDING 等补投。

    解析任务留到调用时再导入。完成上传会经过这里，而任务包初始化
    又会回到完成上传，模块顶部导入会形成环。
    """
    from task.parse_document import parse_document

    try:
        parse_document.delay(task_id)
    except Exception:
        logger.exception("解析任务投递失败: %s", task_id)
        return False
    return True
