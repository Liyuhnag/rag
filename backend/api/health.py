# -*- coding: utf-8 -*-
"""健康检查。"""

from fastapi import APIRouter

from models.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")
