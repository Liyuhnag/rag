# -*- coding: utf-8 -*-
"""对象存储。文件正文放在 S3 兼容服务里。"""

import hashlib
import hmac
from datetime import datetime
from datetime import timezone
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import quote
from urllib.parse import urlparse
from urllib.request import Request
from urllib.request import urlopen

from services.exceptions import StorageError


class ObjectStorage:
    """保存和删除原始文件。"""

    def put(self, key: str, content: bytes, content_type: str) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
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
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("S3_ENDPOINT 必须是 http 或 https 地址")
        self.scheme = parsed.scheme
        self.host = parsed.netloc
        self.region = region
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket = bucket

    def put(self, key: str, content: bytes, content_type: str) -> None:
        self._request("PUT", key, content, content_type)

    def delete(self, key: str) -> None:
        self._request("DELETE", key, b"", None)

    def _request(
        self,
        method: str,
        key: str,
        body: bytes,
        content_type: str | None,
    ) -> None:
        path = "/" + self.bucket + "/" + quote(key, safe="/")
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        headers = {
            "host": self.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if content_type:
            headers["content-type"] = content_type
        signed_names = sorted(headers)
        canonical_headers = "".join(
            f"{name}:{headers[name]}\n" for name in signed_names
        )
        signed_headers = ";".join(signed_names)
        canonical_request = "\n".join(
            [
                method,
                path,
                "",
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ]
        )
        signing_key = _sign(
            ("AWS4" + self.secret_access_key).encode(),
            date_stamp,
        )
        signing_key = _sign(signing_key, self.region)
        signing_key = _sign(signing_key, "s3")
        signing_key = _sign(signing_key, "aws4_request")
        signature = hmac.new(
            signing_key,
            string_to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        request_headers = {
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": authorization,
        }
        if content_type:
            request_headers["Content-Type"] = content_type
        request = Request(
            f"{self.scheme}://{self.host}{path}",
            data=body if method in {"PUT", "POST", "DELETE"} else None,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:
                response.read()
        except HTTPError as exc:
            raise StorageError("对象存储请求失败") from exc
        except URLError as exc:
            raise StorageError("对象存储不可用") from exc


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()
