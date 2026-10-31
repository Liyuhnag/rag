# -*- coding: utf-8 -*-
"""显式启用的 MySQL 8 租约集成测试；只操作随机前缀的独立测试表。

运行：RAG_TEST_MYSQL=1 .venv/bin/python -m pytest tests/test_task_lease_mysql.py
连接配置从后端 .env 读取，当前用户需有创建/删除测试表的权限。
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from threading import Barrier
from threading import Event
from uuid import uuid4

import pymysql
import pytest
from dotenv import dotenv_values

from model.common import database
from model.document import orm as document_orm
from model.document_task import orm
from model.document_task.lease import LeaseLost
from model.document_task.model import DocumentTaskStage
from model.parse_job import orm as job_orm
from services.task_runtime.lease import PARSE_STAGES
from services.task_runtime.lease import TaskLease

pytestmark = pytest.mark.skipif(
    os.getenv("RAG_TEST_MYSQL") != "1", reason="需要显式启用临时 MySQL 集成测试"
)


@pytest.fixture(scope="module")
def mysql_config():
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    config = {
        "host": os.getenv("MYSQL_HOST", values.get("MYSQL_HOST") or "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", values.get("MYSQL_PORT") or "3306")),
        "user": os.getenv("MYSQL_USER", values.get("MYSQL_USER") or ""),
        "password": os.getenv("MYSQL_PASSWORD", values.get("MYSQL_PASSWORD") or ""),
    }
    config["database"] = os.getenv("MYSQL_DATABASE", values.get("MYSQL_DATABASE") or "")
    prefix = f"lease_test_{uuid4().hex}_"
    tables = ("documents", "document_tasks", "parse_jobs", "document_chunks")
    pattern = re.compile(r"\b(" + "|".join(tables) + r")\b")

    class IsolatedCursor(pymysql.cursors.DictCursor):
        def execute(self, query, args=None):
            if isinstance(query, (bytes, bytearray)):
                query = query.decode("utf-8")
            query = pattern.sub(lambda match: prefix + match.group(), query)
            return super().execute(query, args)

    original_connect = pymysql.connect
    created = []
    with original_connect(
        **config, autocommit=True, cursorclass=IsolatedCursor
    ) as setup:
        try:
            with setup.cursor() as cursor:
                for statement in database._schema_statements(database._SCHEMA_SQL):
                    if any(
                        f"CREATE TABLE IF NOT EXISTS {table} (" in statement
                        for table in tables
                    ):
                        cursor.execute(statement)
                        created.append(re.search(r"EXISTS (\w+)", statement).group(1))

            def connect(**kwargs):
                kwargs["cursorclass"] = IsolatedCursor
                return original_connect(**kwargs)

            yield config, connect
        finally:
            with setup.cursor() as cursor:
                for table in reversed(created):
                    cursor.execute(f"DROP TABLE {table}")


@pytest.fixture
def mysql(mysql_config, monkeypatch):
    config, connect = mysql_config
    monkeypatch.setattr(database, "_db", config)
    monkeypatch.setattr(database.pymysql, "connect", connect)
    for table in ("parse_jobs", "document_tasks", "document_chunks", "documents"):
        database.execute(f"DELETE FROM {table}")
    now = datetime.now(timezone.utc)
    database.execute(
        """INSERT INTO documents
        (id,name,file_type,size_bytes,chunk_count,status,object_key,file_url,
         previewable,parse_version,uploaded_at,updated_at)
        VALUES ('doc','demo.txt','txt',4,0,'PROCESSING','source','',0,1,%(now)s,%(now)s)""",
        {"now": database.as_utc(now)},
    )
    database.execute(
        """INSERT INTO document_tasks
        (id,document_id,version,status,stage,created_at,updated_at)
        VALUES ('task','doc',1,'PENDING','PENDING',%(now)s,%(now)s)""",
        {"now": database.as_utc(now)},
    )
    return now


def test_only_one_concurrent_worker_can_claim(mysql):
    start = Barrier(2)

    def claim():
        start.wait(timeout=2)
        return orm.claim("task", mysql)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        claimed = [future.result(timeout=3) for future in futures]
    winners = [task for task in claimed if task is not None]
    assert len(winners) == 1
    assert orm.get("task").lease_token == winners[0].lease_token


def test_replaced_lease_cannot_change_task_or_document(mysql):
    old = orm.claim("task", mysql)
    database.execute("UPDATE document_tasks SET status='PENDING' WHERE id='task'")
    new = orm.claim("task", mysql)
    assert old.lease_token != new.lease_token
    assert not orm.complete("task", "old complete", mysql, old.lease_token)
    assert not orm.fail("task", "old fail", mysql, old.lease_token)
    assert not orm.update_progress(
        "task", DocumentTaskStage.CHUNKING, 85, "old progress", mysql, old.lease_token
    )
    assert not orm.begin_merge("task", mysql, old.lease_token)
    assert not orm.renew("task", old.lease_token, mysql, PARSE_STAGES)
    with pytest.raises(LeaseLost):
        with TaskLease("task", old.lease_token).writes():
            document_orm.mark_failed("doc", "old fail", mysql, expected_version=1)
    assert orm.get("task").lease_token == new.lease_token
    assert orm.get("task").status == "RUNNING"
    assert document_orm.get("doc").status == "PROCESSING"
    assert orm.complete("task", "new complete", mysql, new.lease_token)


def test_nested_business_writes_share_transaction_and_rollback(mysql):
    task = orm.claim("task", mysql)
    with pytest.raises(RuntimeError, match="rollback"):
        with TaskLease("task", task.lease_token).writes():
            document_orm.set_parsed_artifacts(
                "doc", "md", "json", mysql, expected_version=1
            )
            with database.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE documents SET chunk_count=17 WHERE id='doc'")
            assert orm.fail("task", "business fail", mysql, task.lease_token)
            document_orm.mark_failed("doc", "business fail", mysql, expected_version=1)
            assert document_orm.get("doc").parsed_md_key == "md"
            raise RuntimeError("rollback")
    document = document_orm.get("doc")
    assert document.parsed_md_key is None
    assert document.chunk_count == 0
    assert document.status == "PROCESSING"
    assert orm.get("task").status == "RUNNING"
    # ContextVar 必须在退出后复原；后续独立更新应当正常提交。
    assert orm.complete("task", "done", mysql, task.lease_token)
    assert orm.get("task").status == "COMPLETED"


def test_guard_blocks_recovery_and_refreshes_time_before_commit(mysql):
    task = orm.claim("task", mysql)
    old = mysql - timedelta(hours=1)
    orm.update_progress(
        "task", DocumentTaskStage.CHUNKING, 85, "chunk", old, task.lease_token
    )
    attempted = Event()

    def recover():
        attempted.set()
        return orm.recover_chunking(mysql - timedelta(minutes=5), mysql, 10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with TaskLease("task", task.lease_token).writes():
            future = pool.submit(recover)
            assert attempted.wait(2)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
            document_orm.set_parsed_artifacts(
                "doc", "md", "json", mysql, expected_version=1
            )
        assert future.result(timeout=3) == []
    assert orm.get("task").status == "RUNNING"
    assert document_orm.get("doc").parsed_md_key == "md"


def test_background_thread_uses_independent_connection(mysql):
    task = orm.claim("task", mysql)
    seen = Event()
    with TaskLease("task", task.lease_token, interval=0.01) as lease:
        with lease.writes():
            # 主线程事务中的变更尚未提交，旁路线程只能读到 PROCESSING。
            document_orm.mark_failed("doc", "uncommitted", mysql, expected_version=1)

            def read():
                assert document_orm.get("doc").status == "PROCESSING"
                seen.set()

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(read)
                assert seen.wait(2)
                future.result(timeout=2)
        # 心跳续租同样不能共享主线程连接。
        assert not lease._lost.is_set()


def test_same_timestamp_renew_is_not_a_lost_lease(mysql):
    task = orm.claim("task", mysql)
    assert orm.renew("task", task.lease_token, mysql, PARSE_STAGES)
    assert orm.renew("task", task.lease_token, mysql, PARSE_STAGES)
    assert not orm.renew("task", task.lease_token, mysql, (DocumentTaskStage.INDEXING,))


def test_mineru_jobs_are_grouped_and_polled_by_original_claim(mysql):
    from tests.test_poll_parse import _job

    task = orm.claim("task", mysql)
    old = _job(
        id="old",
        document_id="doc",
        task_id="task",
        version=1,
        lease_token=task.lease_token,
    )
    job_orm.insert(old)
    database.execute("UPDATE document_tasks SET status='PENDING' WHERE id='task'")
    new = orm.claim("task", mysql)
    current = _job(
        id="new",
        document_id="doc",
        task_id="task",
        version=1,
        lease_token=new.lease_token,
    )
    job_orm.insert(current)
    assert [
        job.id for job in job_orm.list_by_document_version("doc", 1, new.lease_token)
    ] == ["new"]
    assert [job.id for job in job_orm.claim_due(mysql, 10)] == ["new"]
    assert job_orm.get("old").lease_token == old.lease_token


def test_chunk_transaction_rolls_back_with_outer_lease_transaction(mysql):
    from model.document_chunk import orm as chunk_orm
    from services.chunking.common.models import ChunkOptions
    from services.chunking.token import TokenChunker
    from services.parsing.models import ParseRequest
    from services.parsing.models import ParsedBlock
    from services.parsing.models import ParsedDocument

    task = orm.claim("task", mysql)
    parsed = ParsedDocument(
        ParseRequest("doc", 1, "source", "txt", "demo.txt", 4),
        "demo",
        [ParsedBlock("text", "original text")],
    )
    chunks = list(TokenChunker().iter_split(parsed, ChunkOptions()))
    assert chunk_orm.replace_current_version("doc", 1, chunks)
    original = chunk_orm.list_by_version("doc", 1)
    with pytest.raises(RuntimeError, match="outer rollback"):
        with TaskLease("task", task.lease_token).writes():
            assert chunk_orm.replace_current_version("doc", 1, [])
            assert chunk_orm.count_by_version("doc", 1) == 0
            raise RuntimeError("outer rollback")
    assert chunk_orm.list_by_version("doc", 1) == original
    assert document_orm.get("doc").chunk_count == 1


def test_migration_never_assigns_current_token_to_historical_jobs(mysql):
    from tests.test_poll_parse import _job

    task = orm.claim("task", mysql)
    job_orm.insert(
        _job(
            id="legacy",
            document_id="doc",
            task_id="task",
            version=1,
            lease_token=task.lease_token,
        )
    )
    # 重建升级前的列与唯一键，再实际执行迁移脚本。
    database.execute(
        """ALTER TABLE parse_jobs
        DROP INDEX uk_parse_jobs_document_version_part,
        DROP COLUMN lease_token,
        ADD UNIQUE KEY uk_parse_jobs_document_version_part (document_id,version,part_index)"""
    )
    migration = (
        Path(__file__).resolve().parents[1] / "db/migrations/0003_parse_job_lease.sql"
    )
    for statement in database._schema_statements(migration.read_text(encoding="utf-8")):
        database.execute(statement)
    assert job_orm.get("legacy").lease_token is None
    assert job_orm.claim_due(mysql, 10) == []
    job_orm.insert(
        _job(
            id="current",
            document_id="doc",
            task_id="task",
            version=1,
            lease_token=task.lease_token,
        )
    )
    assert [
        job.id for job in job_orm.list_by_document_version("doc", 1, task.lease_token)
    ] == ["current"]
