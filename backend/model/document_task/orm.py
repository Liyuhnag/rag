# -*- coding: utf-8 -*-
"""document_tasks 表的 SQL。"""

from model.common.database import execute
from model.document_task.model import DocumentTask


def _from_row(row: dict[str, object]) -> DocumentTask:
    return DocumentTask(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        version=int(row["version"]),
        status=str(row["status"]),
        stage=str(row["stage"]),
        progress=int(row["progress"]),
        step=str(row["step"]),
        error_message=(
            None if row["error_message"] is None else str(row["error_message"])
        ),
    )


def find(document_id: str, version: int) -> DocumentTask | None:
    rows = execute(
        """
        SELECT * FROM document_tasks
        WHERE document_id = %(document_id)s AND version = %(version)s
        LIMIT 1
        """,
        {"document_id": document_id, "version": version},
    )
    return None if not rows else _from_row(rows[0])


def get(task_id: str) -> DocumentTask | None:
    rows = execute(
        "SELECT * FROM document_tasks WHERE id = %(id)s LIMIT 1",
        {"id": task_id},
    )
    return None if not rows else _from_row(rows[0])
