# -*- coding: utf-8 -*-
"""DashScope OpenAI 兼容 embedding 接口。"""

import math
import logging
import time

import httpx

from config import Settings

logger = logging.getLogger("rag.indexing.embedding")


class DashScopeEmbedder:
    def __init__(self, settings: Settings, transport=None, sleep=time.sleep):
        if not settings.dashscope_api_key:
            raise RuntimeError("请在 .env 中配置 DASHSCOPE_API_KEY")
        self.settings = settings
        self.transport = transport
        self.sleep = sleep

    def embed(self, texts: list[str]) -> list[list[float]]:
        """重试网络故障及 429/5xx，按 index 恢复输入顺序并验证向量。"""
        if not 1 <= len(texts) <= 10:
            raise ValueError("Embedding 单批必须包含 1 到 10 条文本")
        settings = self.settings
        with httpx.Client(
            transport=self.transport,
            timeout=httpx.Timeout(settings.indexing_timeout_seconds, connect=10),
        ) as client:
            return self._embed(client, texts, 0)

    def _embed(self, client: httpx.Client, texts: list[str], depth: int) -> list[list[float]]:
        for attempt in range(4):
            started = time.monotonic()
            try:
                response = client.post(
                    f"{self.settings.embedding_base_url.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {self.settings.dashscope_api_key}"},
                    json={
                        "model": self.settings.embedding_model,
                        "input": texts,
                        "dimensions": self.settings.embedding_dim,
                        "encoding_format": "float",
                    },
                )
            except httpx.TransportError:
                logger.warning(
                    "embedding request transport_error elapsed_ms=%d",
                    int((time.monotonic() - started) * 1000),
                )
                if attempt == 3:
                    raise RuntimeError("Embedding 网络请求失败") from None
            else:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if response.is_success:
                    vectors = self._vectors(response, len(texts))
                    usage = response.json().get("usage") or {}
                    if not isinstance(usage, dict):
                        usage = {}
                    logger.info(
                        "embedding request count=%d tokens=%s elapsed_ms=%d",
                        len(texts), usage.get("total_tokens", "unknown"), elapsed_ms,
                    )
                    return vectors
                logger.warning(
                    "embedding request status=%d count=%d elapsed_ms=%d",
                    response.status_code, len(texts), elapsed_ms,
                )
                if response.status_code == 400 and self._is_length_error(response):
                    return self._split_and_embed(client, texts, depth)
                if response.status_code != 429 and response.status_code < 500:
                    raise RuntimeError(
                        f"Embedding 请求失败（HTTP {response.status_code}），请检查模型配置与权限"
                    )
                if attempt == 3:
                    raise RuntimeError(
                        f"Embedding 重试耗尽（HTTP {response.status_code}）"
                    )
            self.sleep(2**attempt)
        raise RuntimeError("Embedding 未返回结果")

    @staticmethod
    def _is_length_error(response: httpx.Response) -> bool:
        try:
            data = response.json()
        except ValueError:
            return False
        error = data.get("error", data) if isinstance(data, dict) else {}
        if not isinstance(error, dict):
            return False
        detail = f"{error.get('code', '')} {error.get('message', '')}".lower()
        return any(
            marker in detail
            for marker in ("token", "context length", "input length", "too long", "长度", "超限")
        )

    def _split_and_embed(
        self, client: httpx.Client, texts: list[str], depth: int
    ) -> list[list[float]]:
        if depth >= 8:
            raise RuntimeError("Embedding 文本超出模型长度限制，拆分次数已耗尽")
        if len(texts) > 1:
            middle = len(texts) // 2
            return self._embed(client, texts[:middle], depth + 1) + self._embed(
                client, texts[middle:], depth + 1
            )
        text = texts[0]
        if len(text) < 2:
            raise RuntimeError("Embedding 单条文本超出模型长度限制")
        middle = len(text) // 2
        left, right = self._embed(client, [text[:middle]], depth + 1)[0], self._embed(
            client, [text[middle:]], depth + 1
        )[0]
        combined = [
            (a * middle + b * (len(text) - middle)) / len(text)
            for a, b in zip(left, right, strict=True)
        ]
        norm = math.sqrt(sum(value * value for value in combined))
        if not norm:
            raise RuntimeError("Embedding 分段向量无法合并")
        return [[value / norm for value in combined]]

    def _vectors(self, response: httpx.Response, count: int) -> list[list[float]]:
        try:
            data = response.json()["data"]
            ordered = sorted(data, key=lambda item: item["index"])
            if [item["index"] for item in ordered] != list(range(count)):
                raise ValueError("index 不匹配")
            vectors = [item["embedding"] for item in ordered]
            for vector in vectors:
                if len(vector) != self.settings.embedding_dim:
                    raise ValueError("向量维度不匹配")
                if not all(
                    isinstance(value, (int, float)) and math.isfinite(value)
                    for value in vector
                ):
                    raise ValueError("向量包含无效数值")
                if not any(vector):
                    raise ValueError("COSINE 不支持零向量")
            return vectors
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Embedding 返回格式、数量或维度无效") from exc
