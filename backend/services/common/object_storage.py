# -*- coding: utf-8 -*-
"""对象存储。文件正文和分片放在 S3 兼容服务里。"""

from dataclasses import dataclass
from datetime import datetime

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError
from botocore.exceptions import ClientError

from services.common.exceptions import StorageError
from services.common.exceptions import UploadNotFound


@dataclass(frozen=True)
class UploadedPart:
    part_number: int
    etag: str
    size_bytes: int


@dataclass(frozen=True)
class MultipartUploadInfo:
    key: str
    upload_id: str
    initiated: datetime | None


class ObjectStorage:
    """对象存储接口。"""

    def put(self, key: str, content: bytes, content_type: str) -> None:
        raise NotImplementedError

    def get_object(self, key: str) -> bytes:
        raise NotImplementedError

    def download(self, key: str, path: str) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def create_multipart_upload(self, key: str, content_type: str) -> str:
        raise NotImplementedError

    def upload_part(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        body: bytes,
    ) -> str:
        raise NotImplementedError

    def list_parts(self, key: str, upload_id: str) -> list[UploadedPart]:
        raise NotImplementedError

    def complete_multipart_upload(
        self,
        key: str,
        upload_id: str,
        parts: list[dict[str, object]],
    ) -> None:
        raise NotImplementedError

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        raise NotImplementedError

    def head_object(self, key: str) -> int | None:
        raise NotImplementedError

    def list_multipart_uploads(
        self,
        prefix: str,
    ) -> list[MultipartUploadInfo]:
        raise NotImplementedError


class S3ObjectStorage(ObjectStorage):
    """路径风格的 S3 兼容存储。"""

    def __init__(
        self,
        endpoint: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise RuntimeError("S3_ENDPOINT 必须是 http 或 https 地址")
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=10,
                read_timeout=120,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def put(self, key: str, content: bytes, content_type: str) -> None:
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("对象存储写入失败") from exc

    def get_object(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            body = response.get("Body")
            if body is None:
                raise StorageError("对象存储返回空响应")
            return body.read()
        except ClientError as exc:
            if _error_code(exc) in {"NoSuchKey", "404", "NotFound"}:
                raise StorageError("对象不存在") from exc
            raise StorageError("对象存储读取失败") from exc
        except BotoCoreError as exc:
            raise StorageError("对象存储读取失败") from exc

    def download(self, key: str, path: str) -> None:
        """把对象下载到本地文件，避免整份读进内存。"""
        try:
            self._client.download_file(self.bucket, key, path)
        except ClientError as exc:
            if _error_code(exc) in {"NoSuchKey", "404", "NotFound"}:
                raise StorageError("对象不存在") from exc
            raise StorageError("对象存储读取失败") from exc
        except BotoCoreError as exc:
            raise StorageError("对象存储读取失败") from exc

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) == "NoSuchKey":
                return
            raise StorageError("对象存储删除失败") from exc
        except BotoCoreError as exc:
            raise StorageError("对象存储删除失败") from exc

    def create_multipart_upload(self, key: str, content_type: str) -> str:
        try:
            response = self._client.create_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("创建分片上传失败") from exc
        upload_id = response.get("UploadId")
        if not upload_id:
            raise StorageError("对象存储未返回 UploadId")
        return str(upload_id)

    def upload_part(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        body: bytes,
    ) -> str:
        try:
            response = self._client.upload_part(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=body,
            )
        except ClientError as exc:
            if _error_code(exc) == "NoSuchUpload":
                raise UploadNotFound("分片上传已不存在") from exc
            raise StorageError("分片上传失败") from exc
        except BotoCoreError as exc:
            raise StorageError("分片上传失败") from exc
        return str(response.get("ETag", ""))

    def list_parts(self, key: str, upload_id: str) -> list[UploadedPart]:
        parts: list[UploadedPart] = []
        marker = 0
        try:
            while True:
                response = self._client.list_parts(
                    Bucket=self.bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumberMarker=marker,
                )
                for item in response.get("Parts", []):
                    parts.append(
                        UploadedPart(
                            part_number=int(item["PartNumber"]),
                            etag=str(item.get("ETag", "")),
                            size_bytes=int(item.get("Size", 0)),
                        )
                    )
                if not response.get("IsTruncated"):
                    break
                marker = int(response.get("NextPartNumberMarker", 0))
                if marker <= 0:
                    break
        except ClientError as exc:
            if _error_code(exc) == "NoSuchUpload":
                raise UploadNotFound("分片上传已不存在") from exc
            raise StorageError("查询分片失败") from exc
        except BotoCoreError as exc:
            raise StorageError("查询分片失败") from exc
        parts.sort(key=lambda part: part.part_number)
        return parts

    def complete_multipart_upload(
        self,
        key: str,
        upload_id: str,
        parts: list[dict[str, object]],
    ) -> None:
        try:
            self._client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except ClientError as exc:
            if _error_code(exc) == "NoSuchUpload":
                raise UploadNotFound("分片上传已不存在") from exc
            raise StorageError("合并分片失败") from exc
        except BotoCoreError as exc:
            raise StorageError("合并分片失败") from exc

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        try:
            self._client.abort_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
            )
        except ClientError as exc:
            if _error_code(exc) == "NoSuchUpload":
                return
            raise StorageError("取消分片上传失败") from exc
        except BotoCoreError as exc:
            raise StorageError("取消分片上传失败") from exc

    def head_object(self, key: str) -> int | None:
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise StorageError("查询对象失败") from exc
        except BotoCoreError as exc:
            raise StorageError("查询对象失败") from exc
        return int(response.get("ContentLength", 0))

    def list_multipart_uploads(
        self,
        prefix: str,
    ) -> list[MultipartUploadInfo]:
        uploads: list[MultipartUploadInfo] = []
        key_marker = ""
        upload_id_marker = ""
        try:
            while True:
                response = self._client.list_multipart_uploads(
                    Bucket=self.bucket,
                    Prefix=prefix,
                    KeyMarker=key_marker,
                    UploadIdMarker=upload_id_marker,
                )
                for item in response.get("Uploads", []):
                    uploads.append(
                        MultipartUploadInfo(
                            key=str(item["Key"]),
                            upload_id=str(item["UploadId"]),
                            initiated=item.get("Initiated"),
                        )
                    )
                if not response.get("IsTruncated"):
                    break
                key_marker = str(response.get("NextKeyMarker", ""))
                upload_id_marker = str(
                    response.get("NextUploadIdMarker", "")
                )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("查询分片上传列表失败") from exc
        return uploads


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


_storage: dict[str, object] = {
    "objects": None,
    "bucket": "",
    "file_base_url": "",
}


def configure(
    objects: ObjectStorage,
    bucket: str,
    file_base_url: str,
) -> None:
    """保存对象存储客户端、桶和文件链接前缀。"""
    _storage["objects"] = objects
    _storage["bucket"] = bucket
    _storage["file_base_url"] = file_base_url


def client() -> ObjectStorage:
    """返回当前对象存储客户端。尚未配置时失败。"""
    objects = _storage["objects"]
    if not isinstance(objects, ObjectStorage):
        raise StorageError("对象存储不可用")
    return objects


def bucket() -> str:
    return str(_storage["bucket"])


def file_base_url() -> str:
    return str(_storage["file_base_url"])
