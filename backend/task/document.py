# -*- coding: utf-8 -*-
"""文档异步处理。先写入对象存储和文档记录。"""

from pathlib import Path

from services.document import store as document_store
from task import record


def process_document(task_id: str) -> None:
    """执行一条文档处理任务。

    当前完成对象存储和文档入库。文本分块、向量化接在这次入库之后。
    """
    task = record.get(task_id)
    if task is None:
        return
    path = Path(task.temp_path)
    record.set_status(task_id, "STORING")
    try:
        document = document_store.add(
            task.name,
            task.file_type,
            path.read_bytes(),
        )
    except Exception as exc:
        record.fail(task_id, str(exc) or "处理失败")
        raise
    finally:
        path.unlink(missing_ok=True)
    record.finish(task_id, document.id)
