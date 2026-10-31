# -*- coding: utf-8 -*-
"""索引环境配置加载与启动校验。"""

import pytest

import config


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
    return monkeypatch


def test_indexing_env_is_used(env):
    env.setenv("MILVUS_URI", "http://milvus:19530")
    env.setenv("MILVUS_COLLECTION", "chunks_v2")
    env.setenv("EMBEDDING_DIM", "512")
    env.setenv("DASHSCOPE_API_KEY", "test-key")
    env.setenv("ES_URL", "https://es:9200")
    env.setenv("ES_BULK_SIZE", "250")
    env.setenv("ES_ANALYZER", "standard")
    env.setenv("ES_SEARCH_ANALYZER", "standard")
    settings = config.load_settings()
    assert settings.milvus_uri == "http://milvus:19530"
    assert settings.milvus_collection == "chunks_v2"
    assert settings.embedding_dim == 512
    assert settings.dashscope_api_key == "test-key"
    assert settings.es_url == "https://es:9200"
    assert settings.es_bulk_size == 250
    assert settings.es_analyzer == settings.es_search_analyzer == "standard"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("INDEXING_BATCH_SIZE", "11"),
        ("INDEXING_BATCH_SIZE", "0"),
        ("EMBEDDING_DIM", "invalid"),
        ("INDEXING_TIMEOUT_SECONDS", "0"),
        ("ES_BULK_SIZE", "0"),
        ("MILVUS_COLLECTION", ""),
    ],
)
def test_invalid_indexing_configuration_fails_at_startup(env, key, value):
    env.setenv(key, value)
    with pytest.raises(RuntimeError, match=key):
        config.load_settings()
