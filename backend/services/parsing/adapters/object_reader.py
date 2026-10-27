# -*- coding: utf-8 -*-
"""对象存储适配器。"""

from pathlib import Path

from services.common.object_storage import client as storage_client


class StorageObjectReader:
    """把 Worker 的对象读取接口接到 S3 客户端。"""

    def read_object(self, key: str) -> bytes:
        """读取完整对象。"""
        return storage_client().get_object(key)

    def download_object(self, key: str, path: Path) -> None:
        """把对象下载到本地文件。"""
        storage_client().download(key, str(path))

    def upload_file(self, key: str, path: Path, content_type: str) -> None:
        """从本地文件上传对象。"""
        storage_client().upload_file(key, str(path), content_type)

    def write_object(self, key: str, content: bytes, content_type: str) -> None:
        """写入解析产物。"""
        storage_client().put(key, content, content_type)
