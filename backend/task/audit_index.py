# -*- coding: utf-8 -*-
"""已发布文档的索引一致性只读巡检。"""

import logging

from model.document import orm as document_orm
from model.document.model import DocumentStatus
from services.indexing.service import DocumentIndexingService
from task.app import celery_app

logger = logging.getLogger("rag.task")


@celery_app.task(name="rag.audit_document_index", queue="maintenance")
def audit_document_index(document_id: str) -> bool:
    document = document_orm.get(document_id)
    if document is None or document.status != DocumentStatus.READY:
        return False
    try:
        return DocumentIndexingService().audit(document, document.parse_version)
    except Exception:
        logger.exception("文档索引一致性巡检失败: %s", document_id)
        raise
