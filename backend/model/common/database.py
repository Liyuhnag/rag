# -*- coding: utf-8 -*-
"""数据库连接。各模型的 ORM 通过这里执行 SQL。"""

from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Iterator

import pymysql
from pymysql.err import Error as MySQLError
from pymysql.err import IntegrityError

from services.common.exceptions import DuplicateRecord
from services.common.exceptions import StorageError


_SCHEMA_SQL = (
    Path(__file__).resolve().parents[2] / "db" / "schema.sql"
).read_text(encoding="utf-8")

_db = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "",
    "password": "",
    "database": "",
}


def configure(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> None:
    """保存连接信息，并按 schema.sql 建表。"""
    _db["host"] = host
    _db["port"] = port
    _db["user"] = user
    _db["password"] = password
    _db["database"] = database
    for statement in _schema_statements(_SCHEMA_SQL):
        execute(statement, {})


def _schema_statements(sql: str) -> list[str]:
    """去掉注释后按语句拆开。pymysql 一次只执行一条。"""
    lines = [
        line
        for line in sql.splitlines()
        if not line.strip().startswith("--")
    ]
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]


def as_utc(value: datetime) -> datetime:
    """写入 DATETIME 前转成不带时区的 UTC 时间。"""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def as_aware(value: object) -> datetime:
    """从 DATETIME 读出后按 UTC 补上时区。"""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    if isinstance(value, datetime):
        return value
    raise StorageError("数据库不可用")


def execute(
    sql: str,
    params: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """执行一条 SQL。查询返回行，写入语句返回空列表。"""
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params or {})
                if cursor.description is None:
                    return []
                return list(cursor.fetchall())
    except StorageError:
        raise
    except IntegrityError as exc:
        raise DuplicateRecord(str(exc)) from exc
    except MySQLError as exc:
        raise StorageError("数据库不可用") from exc


def execute_count(
    sql: str,
    params: dict[str, object] | None = None,
) -> int:
    """执行写入语句并返回受影响行数。"""
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params or {})
                return int(cursor.rowcount)
    except StorageError:
        raise
    except IntegrityError as exc:
        raise DuplicateRecord(str(exc)) from exc
    except MySQLError as exc:
        raise StorageError("数据库不可用") from exc


@contextmanager
def transaction() -> Iterator[pymysql.Connection]:
    """在一个关闭自动提交的连接上执行多条语句。"""
    try:
        connection = pymysql.connect(
            host=str(_db["host"]),
            port=int(_db["port"]),
            user=str(_db["user"]),
            password=str(_db["password"]),
            database=str(_db["database"]),
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
    except MySQLError as exc:
        raise StorageError("数据库不可用") from exc

    try:
        yield connection
    except MySQLError as exc:
        connection.rollback()
        raise StorageError("数据库不可用") from exc
    except Exception:
        connection.rollback()
        raise
    else:
        try:
            connection.commit()
        except MySQLError as exc:
            connection.rollback()
            raise StorageError("数据库不可用") from exc
    finally:
        connection.close()


def _connect() -> pymysql.Connection:
    try:
        return pymysql.connect(
            host=str(_db["host"]),
            port=int(_db["port"]),
            user=str(_db["user"]),
            password=str(_db["password"]),
            database=str(_db["database"]),
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
    except MySQLError as exc:
        raise StorageError("数据库不可用") from exc
