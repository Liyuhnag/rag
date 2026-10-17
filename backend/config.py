# -*- coding: utf-8 -*-
"""进程配置。环境变量只在启动时读取。"""

import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path


DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_OPERATOR = "当前用户"
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


def _default_upload_dir() -> Path:
    return Path(__file__).resolve().parent / "data" / "uploads"


def _cors_origins_from_env() -> tuple[str, ...]:
    raw = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    """API 进程使用的配置。"""

    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    upload_dir: Path = field(default_factory=_default_upload_dir)
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )


def load_settings() -> Settings:
    """从环境变量构造配置。非法数值在启动时直接失败。"""
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

    upload_dir_raw = os.getenv("UPLOAD_DIR")
    if upload_dir_raw:
        upload_dir = Path(upload_dir_raw)
    else:
        upload_dir = _default_upload_dir()
    return Settings(
        max_upload_bytes=max_upload_bytes,
        upload_dir=upload_dir,
        cors_origins=_cors_origins_from_env(),
    )
