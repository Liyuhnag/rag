# -*- coding: utf-8 -*-
"""文档异步处理。先写入对象存储和文档记录。"""

from pathlib import Path

from model.task import orm as task_orm
from services.document import store as document_store
from task.celery_app import celery_app


@celery_app.task(name="rag.process_document", ignore_result=True)
def process_document(task_id: str) -> None:
    """执行一条文档处理任务。

    当前完成对象存储和文档入库。文本分块、向量化接在这次入库之后。
    """
    task = task_orm.get(task_id)
    if task is None:
        return
    path = Path(task.temp_path)
    task_orm.set_status(task_id, "STORING")
    try:
        document = document_store.add(
            task.name,
            task.file_type,
            path.read_bytes(),
        )
    except Exception as exc:
        task_orm.fail(task_id, str(exc) or "处理失败")
        raise
    finally:
        path.unlink(missing_ok=True)
    task_orm.finish(task_id, document.id)
