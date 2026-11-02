# -*- coding: utf-8 -*-
"""合并已下载的 MinerU 分段压缩包，发布解析产物并交接切分。"""

from datetime import datetime
from datetime import timezone

from model.document import orm as document_orm
from model.document.model import Document
from model.document_task.model import DocumentTask
from model.document_task.model import DocumentTaskStage
from model.parse_job import orm as parse_job_orm
from model.parse_job.model import ParseJob
from model.parse_job.model import ParseJobStatus
from services.indexing.parsing.collect import PartZip
from services.indexing.parsing.ports import ObjectReader
from services.indexing.parsing.service import DocumentParsingService
from services.task_runtime.lease import TaskLease
from services.task_runtime.lifecycle import advance


def all_downloaded(jobs: list[ParseJob]) -> bool:
    """全部分段均已完成下载（COMPLETED）且已写入 ZIP 对象 key。"""
    if not jobs:
        return False
    return all(
        item.status == ParseJobStatus.COMPLETED and bool(item.result_key)
        for item in jobs
    )


class DocumentMergeService:
    """独立 MERGING 阶段服务；发布解析指针与交接 CHUNKING 原子提交。"""

    def __init__(self, reader: ObjectReader | None = None) -> None:
        self._reader = reader

    def run(
        self,
        task: DocumentTask,
        document: Document,
        lease: TaskLease,
    ) -> None:
        with lease.writes():
            jobs = parse_job_orm.adopt_task_jobs(
                task.id, task.document_id, task.version, lease.token
            )
        if not all_downloaded(jobs):
            advance(lease, DocumentTaskStage.PARSING, 5, "分段未齐，继续等待解析")
            return
        parts = [
            PartZip(item.part_index, item.page_start, item.page_end, item.result_key)
            for item in jobs
        ]
        lease.ensure()
        stored = DocumentParsingService(self._reader, attempt_id=lease.token).collect(
            document.id,
            task.version,
            document.file_type,
            document.object_key,
            parts,
        )

        def publish() -> None:
            document_orm.set_parsed_artifacts(
                document.id,
                stored.md_key,
                stored.json_key,
                datetime.now(timezone.utc),
                expected_version=task.version,
            )
            # result_key 在此仍保留 ZIP，避免恢复时误把 manifest 当作压缩包。

        advance(lease, DocumentTaskStage.CHUNKING, 85, "等待切分", prepare=publish)


def run_merge(
    task: DocumentTask,
    document: Document,
    lease: TaskLease,
    reader: ObjectReader | None = None,
) -> None:
    """独立 MERGING 入口函数。"""
    DocumentMergeService(reader).run(task, document, lease)
