# -*- coding: utf-8 -*-
"""健康检查响应。"""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
