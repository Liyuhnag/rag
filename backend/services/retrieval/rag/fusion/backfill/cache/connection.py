# -*- coding: utf-8 -*-
"""独立 Redis 连接及短超时、熔断边界；不缓存业务数据。"""

import logging
import time
from collections.abc import Callable
from threading import Lock
from typing import TypeVar

logger = logging.getLogger("rag.retrieval.backfill")
_Result = TypeVar("_Result")


class RedisConnection:
    """单个 Redis 实例的连接池及线程安全熔断状态。"""

    def __init__(
        self,
        url: str = "",
        client=None,
        *,
        timeout_seconds: float = 0.2,
        failure_threshold: int = 3,
        recovery_seconds: float = 5.0,
        clock=time.monotonic,
    ) -> None:
        self.url = url.strip()
        self._client = client
        self.timeout_seconds = max(0.01, timeout_seconds)
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = max(0.01, recovery_seconds)
        self._clock = clock
        self._lock = Lock()
        self._failures = 0
        self._retry_at = 0.0
        self._probing = False

    def run(
        self,
        operation: Callable,
        fallback: _Result,
        *,
        deadline: float | None = None,
    ) -> _Result:
        """Redis 故障降级；请求截止后不再发送新操作。"""
        now = self._clock()
        if deadline is not None and now >= deadline:
            raise TimeoutError("父块读取已超时")
        with self._lock:
            if self._failures >= self.failure_threshold:
                if now < self._retry_at or self._probing:
                    return fallback
                self._probing = True
        try:
            with self._lock:
                if self._client is None and self.url:
                    import redis
                    from redis.backoff import NoBackoff
                    from redis.retry import Retry

                    self._client = redis.Redis.from_url(
                        self.url,
                        decode_responses=False,
                        socket_connect_timeout=self.timeout_seconds,
                        socket_timeout=self.timeout_seconds,
                        retry_on_timeout=False,
                        retry=Retry(NoBackoff(), 0),
                        max_connections=32,
                    )
                client = self._client
            if client is None:
                with self._lock:
                    self._probing = False
                return fallback
            result = operation(client)
        except Exception as exc:
            # Redis 是可降级的集成边界，不把故障伪装成父块不存在。
            with self._lock:
                self._failures += 1
                self._probing = False
                self._retry_at = self._clock() + self.recovery_seconds
            logger.warning("父块 Redis 操作失败，转入受限回源：%s", type(exc).__name__)
            return fallback
        with self._lock:
            self._failures = 0
            self._probing = False
        if deadline is not None and self._clock() >= deadline:
            raise TimeoutError("父块读取已超时")
        return result

    def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
        if client is not None:
            client.close()
