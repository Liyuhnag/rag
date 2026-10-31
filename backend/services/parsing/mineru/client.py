# -*- coding: utf-8 -*-
"""MinerU v4 单文件批次客户端。

官方契约：https://mineru.net/doc/docs/index.html
申请上传链接后直接 PUT 文件，服务端自动提交解析；job_id 即 batch_id。
"""

import json
import logging
import os
import time
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import BinaryIO
from typing import Literal
from typing import Protocol
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import quote
from urllib.parse import urlsplit
from urllib.request import Request
from urllib.request import urlopen
from uuid import uuid4

from services.parsing.errors import MinerUJobLost
from services.parsing.errors import MinerURequestError
from services.parsing.errors import ParserUnavailable

logger = logging.getLogger(__name__)

_FILES_PATH = "/api/v4/file-urls/batch"
_JOBS_PATH = "/api/v4/extract-results/batch"
_READ_TIMEOUT = 60
_TRANSFER_TIMEOUT = 300
_MAX_ATTEMPTS = 3
_TRANSFER_CHUNK_SIZE = 64 * 1024
_MAX_JSON_RESPONSE = 1024 * 1024
_RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})
_RETRYABLE_CODES = frozenset({-10001, -60001, -60007, -60009})
MinerUState = Literal["queued", "running", "completed", "failed"]
_STATES: dict[str, MinerUState] = {
    "waiting-file": "queued",
    "pending": "queued",
    "running": "running",
    "converting": "running",
    "done": "completed",
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
    """控制面响应有界；上传 iterable 必须可重放并显式指定 Content-Length。"""

    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | Iterable[bytes] | None,
        timeout: float,
    ) -> HttpResponse:
        """返回响应。连接失败时抛出 OSError。"""
        ...

    def download(
        self,
        url: str,
        headers: dict[str, str],
        target: Path,
        timeout: float,
    ) -> HttpResponse:
        """流式写入 target，成功 body 为空；失败不得发布最终文件。"""
        ...


class UrllibTransport:
    """用标准库发请求。timeout 同时限制连接和读取。"""

    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | Iterable[bytes] | None,
        timeout: float,
    ) -> HttpResponse:
        """发送请求。4xx 和 5xx 作为响应返回，由客户端决定是否重试。"""
        request = _UploadRequest(url, data=body, headers=headers, method=method)
        authorization = request.headers.pop("Authorization", None)
        if authorization:
            # Authorization 不应被 urllib 的重定向复制到其它地址。
            request.add_unredirected_header("Authorization", authorization)
        try:
            with urlopen(request, timeout=timeout) as response:
                return HttpResponse(response.status, _read_json_response(response))
        except HTTPError as exc:
            with exc:
                return HttpResponse(exc.code, exc.read(_MAX_JSON_RESPONSE))
        except URLError as exc:
            raise OSError(str(exc.reason)) from exc
        except HTTPException as exc:
            raise OSError("MinerU HTTP 传输中断") from exc

    def download(
        self,
        url: str,
        headers: dict[str, str],
        target: Path,
        timeout: float,
    ) -> HttpResponse:
        """以固定缓冲写文件，异常时删除不完整的临时产物。"""
        request = Request(url, headers=headers, method="GET")
        completed = False
        try:
            with urlopen(request, timeout=timeout) as response:
                if not 200 <= response.status < 300:
                    return HttpResponse(response.status, b"")
                length_header = response.headers.get("Content-Length")
                try:
                    expected = int(length_header) if length_header is not None else None
                except ValueError as exc:
                    raise OSError("MinerU 下载长度无效") from exc
                received = 0
                with target.open("wb") as output:
                    while chunk := response.read(_TRANSFER_CHUNK_SIZE):
                        output.write(chunk)
                        received += len(chunk)
                if expected is not None and received != expected:
                    raise OSError("MinerU 下载内容不完整")
                completed = True
                return HttpResponse(response.status, b"")
        except HTTPError as exc:
            with exc:
                return HttpResponse(exc.code, b"")
        except URLError as exc:
            raise OSError(str(exc.reason)) from exc
        except HTTPException as exc:
            raise OSError("MinerU HTTP 传输中断") from exc
        finally:
            # 客户端负责成功发布；传输失败时也不保留半个 ZIP。
            if not completed:
                target.unlink(missing_ok=True)


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
        """创建单文件批次并流式上传，上传成功即由服务端自动提交。"""
        body = FileBody(file_path, file_path.stat().st_size)
        response = self._request(
            "POST",
            _FILES_PATH,
            json.dumps(
                {
                    "files": [{"name": Path(filename).name, "data_id": sha256}],
                    "model_version": "vlm",
                }
            ).encode(),
            {"Content-Type": "application/json"},
            _READ_TIMEOUT,
        )
        data = _business_data(response.body)
        batch_id = _check_job_id(_require_id(data, "batch_id"))
        urls = data.get("file_urls")
        if not isinstance(urls, list) or len(urls) != 1:
            raise MinerURequestError("MinerU 必须返回一个上传链接")
        self._request(
            "PUT",
            _signed_url(urls[0]),
            body,
            {"Content-Length": str(body.size)},
            _TRANSFER_TIMEOUT,
            signed=True,
        )
        logger.info("MinerU 任务已提交: job_id=%s", batch_id)
        return MinerUJob(file_id=sha256, job_id=batch_id)

    def _result(self, job_id: str) -> dict[str, object]:
        """重新查询单文件批次，不依赖进程内提交记录。"""
        safe_id = _check_job_id(job_id)
        response = self._request(
            "GET",
            f"{_JOBS_PATH}/{quote(safe_id, safe='')}",
            None,
            {},
            _READ_TIMEOUT,
            lost_on_404=True,
        )
        data = _business_data(response.body)
        results = data.get("extract_result")
        if (
            not isinstance(results, list)
            or len(results) != 1
            or not isinstance(results[0], dict)
        ):
            raise MinerURequestError("MinerU 必须返回一个文件结果")
        return results[0]

    def status(self, job_id: str) -> MinerUStatus:
        """把官方批次文件状态转换为稳定的公共状态。"""
        result = self._result(job_id)
        found = _STATES.get(str(result.get("state", "")))
        if found is None:
            raise MinerURequestError("MinerU 返回了未知状态")
        error = result.get("err_msg")
        error_text = None if error in (None, "") else str(error)
        if error_text and self._api_key:
            error_text = error_text.replace(self._api_key, "[redacted]")
        return MinerUStatus(state=found, error=error_text)

    def download(self, job_id: str, target_dir: Path) -> Path:
        """查询结果后从签名地址下载 ZIP，完整传输后原子发布。"""
        safe_id = _check_job_id(job_id)
        result = self._result(safe_id)
        if result.get("state") != "done":
            raise MinerURequestError("MinerU 任务尚未完成，无法下载")
        url = _signed_url(result.get("full_zip_url"))
        target_dir.mkdir(parents=True, exist_ok=True)
        path = (target_dir / f"{safe_id}.zip").resolve()
        if target_dir.resolve() not in path.parents:
            raise MinerURequestError("MinerU job_id 无效")
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.part")
        try:
            self._request(
                "GET",
                url,
                None,
                {},
                _TRANSFER_TIMEOUT,
                lost_on_404=True,
                target=temporary,
                signed=True,
            )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | Iterable[bytes] | None,
        headers: dict[str, str],
        timeout: float,
        lost_on_404: bool = False,
        target: Path | None = None,
        signed: bool = False,
    ) -> HttpResponse:
        """发送请求。429、408 和 5xx 退避重试，其余 4xx 不重试。"""
        url = path if signed else f"{self._require_base()}{path}"
        request_headers = dict(headers)
        if self._api_key and not signed:
            request_headers["Authorization"] = f"Bearer {self._api_key}"
        for attempt in range(1, self._max_attempts + 1):
            try:
                if target is None:
                    response = self._transport.send(
                        method,
                        url,
                        request_headers,
                        body,
                        timeout,
                    )
                else:
                    target.unlink(missing_ok=True)
                    response = self._transport.download(
                        url,
                        request_headers,
                        target,
                        timeout,
                    )
            except OSError as exc:
                if target is not None:
                    target.unlink(missing_ok=True)
                if attempt == self._max_attempts:
                    logger.warning("MinerU 连接失败")
                    raise ParserUnavailable("MinerU 连接失败") from exc
                self._sleep(float(2 ** (attempt - 1)))
                continue
            if response.status in _RETRYABLE and attempt < self._max_attempts:
                if target is not None:
                    target.unlink(missing_ok=True)
                logger.warning("MinerU 请求将重试: status=%s", response.status)
                self._sleep(float(2 ** (attempt - 1)))
                continue
            checked = _checked(response, lost_on_404)
            if not signed:
                try:
                    _business_data(checked.body)
                except ParserUnavailable:
                    if attempt == self._max_attempts:
                        raise
                    self._sleep(float(2 ** (attempt - 1)))
                    continue
            return checked
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


def _business_data(body: bytes) -> dict[str, object]:
    """检查 HTTP 200 中的官方业务码，错误消息不包含上游原文。"""
    payload = _json_object(body)
    code = payload.get("code")
    if not isinstance(code, (int, str)) or isinstance(code, bool):
        raise MinerURequestError("MinerU 缺少有效业务码")
    if code == -60012:
        raise MinerUJobLost("MinerU 任务不存在或已过期")
    if code in _RETRYABLE_CODES:
        raise ParserUnavailable(f"MinerU 暂不可用: code={code}")
    if code != 0:
        # 仅输出受信任的码类别，避免上游业务码夹带密钥或签名地址。
        if code in {"A0202", "A0211"}:
            raise MinerURequestError("MinerU 认证失败或 Token 已过期")
        raise MinerURequestError("MinerU 拒绝请求: 业务错误")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MinerURequestError("MinerU 没有返回有效 data")
    return data


def _signed_url(value: object) -> str:
    """只允许无内嵌凭证的 HTTP(S) 文件链接。"""
    if not isinstance(value, str) or any(ord(char) < 33 for char in value):
        raise MinerURequestError("MinerU 返回的文件链接无效")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError as exc:
        raise MinerURequestError("MinerU 返回的文件链接无效") from exc
    if not valid:
        raise MinerURequestError("MinerU 返回的文件链接无效")
    return value


class _UploadRequest(Request):
    """签名 PUT 不接受 urllib 自动补上的表单 Content-Type。"""

    def add_unredirected_header(self, key: str, val: str) -> None:
        if self.get_method() == "PUT" and key.lower() == "content-type":
            return
        super().add_unredirected_header(key, val)


@dataclass(frozen=True)
class FileBody:
    """可重放的原始文件流，每次重试重开文件且固定缓冲大小。"""

    path: Path
    size: int

    def __iter__(self) -> Iterator[bytes]:
        with self.path.open("rb") as source:
            if os.fstat(source.fileno()).st_size != self.size:
                raise MinerURequestError("MinerU 上传文件大小已改变")
            remaining = self.size
            while remaining:
                chunk = source.read(min(_TRANSFER_CHUNK_SIZE, remaining))
                if not chunk:
                    raise MinerURequestError("MinerU 上传文件已截断")
                remaining -= len(chunk)
                yield chunk


def _read_json_response(response: BinaryIO) -> bytes:
    """JSON 控制面响应最多 1MiB，额外一字节用于检测超限。"""
    content = response.read(_MAX_JSON_RESPONSE + 1)
    if len(content) > _MAX_JSON_RESPONSE:
        raise MinerURequestError("MinerU 响应体过大")
    return content
