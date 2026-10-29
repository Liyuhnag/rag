# -*- coding: utf-8 -*-
"""DashScope OpenAI 兼容 embedding 接口。"""

import math
import time

import httpx

from config import Settings


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
            for attempt in range(4):
                try:
                    response = client.post(
                        f"{settings.embedding_base_url.rstrip('/')}/embeddings",
                        headers={
                            "Authorization": f"Bearer {settings.dashscope_api_key}"
                        },
                        json={
                            "model": settings.embedding_model,
                            "input": texts,
                            "dimensions": settings.embedding_dim,
                            "encoding_format": "float",
                        },
                    )
                except httpx.TransportError:
                    if attempt == 3:
                        raise RuntimeError("Embedding 网络请求失败") from None
                else:
                    if response.is_success:
                        return self._vectors(response, len(texts))
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
