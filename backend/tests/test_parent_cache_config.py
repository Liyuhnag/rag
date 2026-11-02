# -*- coding: utf-8 -*-
"""父块缓存独立实例配置及数据库超时边界。"""

from datetime import datetime

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import config
from api.common.deps import get_parent_backfiller
from main import create_app
from model.common import database


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)
    for key in (
        "S3_ENDPOINT",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_BUCKET",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_DATABASE",
    ):
        monkeypatch.setenv(key, "test")
    for key in ("REDIS_URL", "PARENT_CACHE_REDIS_URL", "PARENT_GUARD_REDIS_URL"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_separate_redis_defaults_and_custom_addresses(env):
    settings = config.load_settings()
    assert settings.redis_url == "redis://127.0.0.1:6379/0"
    assert settings.parent_cache_redis_url == "redis://127.0.0.1:6380/0"
    assert settings.parent_guard_redis_url == "redis://127.0.0.1:6381/0"
    env.setenv("PARENT_CACHE_REDIS_URL", "rediss://cache:6379/0")
    env.setenv("PARENT_GUARD_REDIS_URL", "redis://guard:6379/0")
    settings = config.load_settings()
    assert settings.parent_cache_redis_url == "rediss://cache:6379/0"
    assert settings.parent_guard_redis_url == "redis://guard:6379/0"


@pytest.mark.parametrize("key", ["PARENT_CACHE_REDIS_URL", "PARENT_GUARD_REDIS_URL"])
@pytest.mark.parametrize(
    "value", ["", "http://cache:6379", "redis://", "redis://cache:invalid"]
)
def test_invalid_redis_address_fails_startup(env, key, value):
    env.setenv(key, value)
    with pytest.raises(RuntimeError, match="Redis|REDIS"):
        config.load_settings()


def test_different_databases_do_not_count_as_separate_instances(env):
    env.setenv("PARENT_CACHE_REDIS_URL", "redis://127.0.0.1:6379/2")
    with pytest.raises(RuntimeError, match="独立 Redis 实例"):
        config.load_settings()


def test_app_reuses_backfiller_and_closes_it_on_shutdown(monkeypatch):
    app = create_app(config.Settings(), lambda settings: None)
    backfiller = app.state.parent_backfiller
    request = Request({"type": "http", "app": app})
    closed = []
    monkeypatch.setattr(backfiller, "close", lambda: closed.append(True))
    with TestClient(app):
        assert get_parent_backfiller(request) is backfiller
        assert get_parent_backfiller(request) is backfiller
    assert closed == [True]
    assert backfiller.redis.connection.url == config.Settings().parent_cache_redis_url
    assert backfiller.redis.guard.url == config.Settings().parent_guard_redis_url
    assert backfiller.bloom.connection is backfiller.redis.guard


def test_database_socket_timeouts_and_legacy_call_compatibility(monkeypatch):
    calls = []

    def connect(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(database.pymysql, "connect", connect)
    database._connect(timeout_seconds=0.25)
    assert calls[-1]["read_timeout"] == calls[-1]["write_timeout"] == 0.25
    assert calls[-1]["connect_timeout"] == 1
    database._connect()
    assert "read_timeout" not in calls[-1]
    with pytest.raises(TimeoutError):
        database._connect(timeout_seconds=0)

    class Cursor:
        description = ("updated_at",)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            pass

        def fetchall(self):
            return [{"updated_at": datetime(2026, 11, 2)}]

    class Connection(Cursor):
        def cursor(self):
            return Cursor()

    monkeypatch.setattr(database, "_connect", lambda: Connection())
    assert (
        database.execute("SELECT updated_at FROM documents")[0]["updated_at"].year
        == 2026
    )
