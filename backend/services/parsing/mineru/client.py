# -*- coding: utf-8 -*-
"""MinerU HTTP 客户端。

调用顺序是创建上传、上传文件、提交任务、查询状态、下载 zip。
下面的路径按这个顺序约定，仓库里没有另一份已抓到的接口说明。
服务端路径不同时，只改这些常量。

文件大小由解析器判断，这里收到文件就提交。
"""

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from typing import Protocol
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request
from urllib.request import urlopen
from uuid import uuid4

from services.parsing.errors import MinerUJobLost
from services.parsing.errors import MinerURequestError
from services.parsing.errors import ParserUnavailable

logger = logging.getLogger(__name__)

# 与设计里的调用生命周期对应，不是从已抓包的 MinerU 文档抄来的。
_FILES_PATH = "/v1/files"
_JOBS_PATH = "/v1/jobs"
_READ_TIMEOUT = 60
_TRANSFER_TIMEOUT = 300
_MAX_ATTEMPTS = 3
_RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})
MinerUState = Literal["queued", "running", "completed", "failed"]
_STATES: dict[str, MinerUState] = {
    "queued": "queued",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
}


@dataclass(frozen=True)
class MinerUJob:
    """一次提交得到的文件和任务编号。"""

    file_id: str
    job_id: str


@dataclass(frozen=True)
class MinerUStatus:
    """MinerU 任务的当前状态。"""

    state: MinerUState
    error: str | None = None


@dataclass(frozen=True)
class HttpResponse:
    """一次 HTTP 调用的状态码和响应体。"""

    status: int
    body: bytes


class Transport(Protocol):
    """发送一次 HTTP 请求。测试注入假实现，避免连真实 MinerU。"""

    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        """返回响应。连接失败时抛出 OSError。"""
        ...


class UrllibTransport:
    """用标准库发请求。timeout 同时限制连接和读取。"""

    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        """发送请求。4xx 和 5xx 作为响应返回，由客户端决定是否重试。"""
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return HttpResponse(response.status, response.read())
        except HTTPError as exc:
            return HttpResponse(exc.code, exc.read())
        except URLError as exc:
            raise OSError(str(exc.reason)) from exc


class MinerUClient:
    """提交文件、查询状态、下载结果 zip。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        transport: Transport | None = None,
        sleep: Callable[[float], None] | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> None:
        self._base_url = _configured(base_url, "MINERU_API_URL").rstrip("/")
        self._api_key = _configured(api_key, "MINERU_API_KEY")
        self._transport = transport or UrllibTransport()
        self._sleep = sleep or _sleep
        self._max_attempts = max_attempts

    def submit(self, file_path: Path, filename: str, sha256: str) -> MinerUJob:
        """上传文件并提交解析任务。调用方不等待解析结束。"""
        content = file_path.read_bytes()
        body, content_type = _multipart(filename, content, sha256)
        uploaded = self._request(
            "POST",
            _FILES_PATH,
            body,
            {"Content-Type": content_type},
            _TRANSFER_TIMEOUT,
        )
        file_id = _require_id(_json_object(uploaded.body), "file_id")
        submitted = self._request(
            "POST",
            _JOBS_PATH,
            json.dumps({"file_id": file_id}).encode(),
            {"Content-Type": "application/json"},
            _READ_TIMEOUT,
        )
        job_id = _require_id(_json_object(submitted.body), "job_id")
        logger.info("MinerU 任务已提交: job_id=%s", job_id)
        return MinerUJob(file_id=file_id, job_id=job_id)

    def status(self, job_id: str) -> MinerUStatus:
        """查询任务状态。404 表示任务索引已丢失。"""
        safe_id = _check_job_id(job_id)
        response = self._request(
            "GET",
            f"{_JOBS_PATH}/{quote(safe_id, safe='')}",
            None,
            {},
            _READ_TIMEOUT,
            lost_on_404=True,
        )
        payload = _json_object(response.body)
        found = _STATES.get(str(payload.get("status", "")).lower())
        if found is None:
            raise MinerURequestError("MinerU 返回了未知状态")
        error = payload.get("error")
        error_text = None if error in (None, "") else str(error)
        if found == "failed":
            logger.info(
                "MinerU 任务失败: job_id=%s error=%s",
                safe_id,
                error_text or "",
            )
        return MinerUStatus(state=found, error=error_text)

    def download(self, job_id: str, target_dir: Path) -> Path:
        """把结果 zip 下载到目标目录，返回 zip 路径。"""
        safe_id = _check_job_id(job_id)
        response = self._request(
            "GET",
            f"{_JOBS_PATH}/{quote(safe_id, safe='')}/result",
            None,
            {},
            _TRANSFER_TIMEOUT,
            lost_on_404=True,
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        path = (target_dir / f"{safe_id}.zip").resolve()
        if target_dir.resolve() not in path.parents:
            raise MinerURequestError("MinerU job_id 无效")
        path.write_bytes(response.body)
        return path

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
        timeout: float,
        lost_on_404: bool = False,
    ) -> HttpResponse:
        """发送请求。429、408 和 5xx 退避重试，其余 4xx 不重试。"""
        url = f"{self._require_base()}{path}"
        request_headers = dict(headers)
        if self._api_key:
            request_headers["Authorization"] = f"Bearer {self._api_key}"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._transport.send(
                    method,
                    url,
                    request_headers,
                    body,
                    timeout,
                )
            except OSError as exc:
                if attempt == self._max_attempts:
                    logger.warning("MinerU 连接失败")
                    raise ParserUnavailable("MinerU 连接失败") from exc
                self._sleep(float(2 ** (attempt - 1)))
                continue
            if (
                response.status in _RETRYABLE
                and attempt < self._max_attempts
            ):
                logger.warning("MinerU 请求将重试: status=%s", response.status)
                self._sleep(float(2 ** (attempt - 1)))
                continue
            return _checked(response, lost_on_404)
        raise ParserUnavailable("MinerU 暂不可用")

    def _require_base(self) -> str:
        """没有地址时拒绝调用，避免请求发到空地址。"""
        if not self._base_url:
            raise ParserUnavailable("MINERU_API_URL 未配置")
        return self._base_url


def _configured(value: str | None, name: str) -> str:
    """显式参数优先；省略时读取环境变量。"""
    if value is not None:
        return value.strip()
    return os.getenv(name, "").strip()


def _sleep(seconds: float) -> None:
    """默认退避等待。测试注入空函数，避免真的睡。"""
    time.sleep(seconds)


def _checked(response: HttpResponse, lost_on_404: bool) -> HttpResponse:
    """把失败状态转成业务异常。成功响应原样返回。"""
    if 200 <= response.status < 300:
        return response
    if response.status == 404 and lost_on_404:
        raise MinerUJobLost("MinerU 任务不存在")
    if response.status >= 500 or response.status in {408, 429}:
        raise ParserUnavailable(f"MinerU 暂不可用: {response.status}")
    raise MinerURequestError(f"MinerU 拒绝请求: {response.status}")


def _json_object(body: bytes) -> dict[str, object]:
    """解析 JSON 对象。响应体不写入异常文本。"""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MinerURequestError("MinerU 返回的不是 JSON") from exc
    if not isinstance(payload, dict):
        raise MinerURequestError("MinerU 返回的不是 JSON 对象")
    return payload


def _require_id(payload: dict[str, object], name: str) -> str:
    """取出非空字符串编号。"""
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise MinerURequestError(f"MinerU 没有返回 {name}")
    return value.strip()


def _check_job_id(job_id: str) -> str:
    """拒绝能改变路径的任务编号。"""
    if (
        not job_id
        or job_id in {".", ".."}
        or "/" in job_id
        or "\\" in job_id
        or any(ord(char) < 32 for char in job_id)
    ):
        raise MinerURequestError("MinerU job_id 无效")
    return job_id


def _multipart(filename: str, content: bytes, sha256: str) -> tuple[bytes, str]:
    """组装文件、文件名和 SHA-256。"""
    boundary = f"mineru{uuid4().hex}"
    safe_name = _header_filename(filename)
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{safe_name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    suffix = (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="filename"\r\n\r\n'
        f"{safe_name}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="sha256"\r\n\r\n'
        f"{sha256}\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    content_type = f"multipart/form-data; boundary={boundary}"
    return prefix + content + suffix, content_type


def _header_filename(filename: str) -> str:
    """只保留文件名，去掉引号和换行。"""
    name = Path(filename).name.replace('"', "").replace("\r", "")
    name = name.replace("\n", "")
    return name or "document"
