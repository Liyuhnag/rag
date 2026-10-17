# -*- coding: utf-8 -*-
"""按进程配置接入数据库和对象存储。"""

from config import Settings
from model.common.database import configure as configure_database
from services.common.object_storage import S3ObjectStorage
from services.common.object_storage import configure as configure_objects


def connect_storage(settings: Settings) -> None:
    """连接数据库，并按配置接入对象存储。"""
    configure_database(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
    )
    configure_objects(
        S3ObjectStorage(
            endpoint=settings.s3_endpoint,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            bucket=settings.s3_bucket,
        ),
        bucket=settings.s3_bucket,
        file_base_url=settings.file_base_url,
    )
