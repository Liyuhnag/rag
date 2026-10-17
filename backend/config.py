# -*- coding: utf-8 -*-
"""进程配置。环境变量只在启动时读取。"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_TASK_QUEUE_LIMIT = 200
ALLOWED_FILE_TYPES = frozenset(
    {
        "pdf",
        "md",
        "markdown",
        "doc",
        "docx",
        "txt",
        "xls",
        "xlsx",
        "csv",
    }
)
ENV_FILE = Path(__file__).resolve().parent / ".env"


def _cors_origins_from_env() -> tuple[str, ...]:
    raw = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} 不能为空")
    return value


def _first_present(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    joined = " 或 ".join(names)
    raise RuntimeError(f"{joined} 不能为空")


@dataclass(frozen=True)
class Settings:
    """API 进程使用的配置。"""

    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    s3_endpoint: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket: str = ""
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = ""
    mysql_password: str = ""
    mysql_database: str = ""
    file_base_url: str = ""
    redis_url: str = "redis://127.0.0.1:6379/0"
    task_queue_limit: int = DEFAULT_TASK_QUEUE_LIMIT


def load_settings() -> Settings:
    """从环境变量构造配置。非法数值在启动时直接失败。"""
    load_dotenv(ENV_FILE, override=False)
    raw_max = os.getenv(
        "MAX_UPLOAD_BYTES",
        str(DEFAULT_MAX_UPLOAD_BYTES),
    )
    try:
        max_upload_bytes = int(raw_max)
    except ValueError as exc:
        raise RuntimeError("MAX_UPLOAD_BYTES 必须是整数") from exc
    if max_upload_bytes < 1:
        raise RuntimeError("MAX_UPLOAD_BYTES 必须大于 0")

    s3_region = os.getenv("S3_REGION", "us-east-1").strip() or "us-east-1"
    mysql_host = os.getenv("MYSQL_HOST", "127.0.0.1").strip() or "127.0.0.1"
    file_base_url = os.getenv("FILE_BASE_URL", "").strip().rstrip("/")
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
    if not redis_url:
        raise RuntimeError("REDIS_URL 不能为空")
    raw_queue_limit = os.getenv(
        "TASK_QUEUE_LIMIT",
        str(DEFAULT_TASK_QUEUE_LIMIT),
    )
    try:
        task_queue_limit = int(raw_queue_limit)
    except ValueError as exc:
        raise RuntimeError("TASK_QUEUE_LIMIT 必须是整数") from exc
    if task_queue_limit < 1:
        raise RuntimeError("TASK_QUEUE_LIMIT 必须大于 0")

    raw_port = os.getenv("MYSQL_PORT", "3306").strip()
    try:
        mysql_port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("MYSQL_PORT 必须是整数") from exc
    if mysql_port < 1 or mysql_port > 65535:
        raise RuntimeError("MYSQL_PORT 必须在 1 到 65535 之间")

    return Settings(
        max_upload_bytes=max_upload_bytes,
        cors_origins=_cors_origins_from_env(),
        s3_endpoint=_required("S3_ENDPOINT"),
        s3_region=s3_region,
        s3_access_key_id=_first_present(
            "S3_ACCESS_KEY_ID",
            "AWS_ACCESS_KEY_ID",
        ),
        s3_secret_access_key=_first_present(
            "S3_SECRET_ACCESS_KEY",
            "AWS_SECRET_ACCESS_KEY",
        ),
        s3_bucket=_required("S3_BUCKET"),
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=_required("MYSQL_USER"),
        mysql_password=_required("MYSQL_PASSWORD"),
        mysql_database=_required("MYSQL_DATABASE"),
        file_base_url=file_base_url,
        redis_url=redis_url,
        task_queue_limit=task_queue_limit,
    )
