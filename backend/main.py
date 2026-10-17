# -*- coding: utf-8 -*-
"""FastAPI 应用入口。"""

import logging
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.chat import router as chat_router
from api.common.errors import register_exception_handlers
from api.documents import router as document_router
from api.health import router as health_router
from api.tasks import router as task_router
from config import Settings
from config import load_settings
from services.chat.service import ChatService
from services.common import connect_storage

logger = logging.getLogger("rag.api")


def create_app(
    settings: Settings | None = None,
    open_storage: Callable[[Settings], None] | None = None,
) -> FastAPI:
    active = load_settings() if settings is None else settings
    if open_storage is None:
        connect_storage(active)
    else:
        open_storage(active)
    application = FastAPI(title="RAG API", version="0.1.0")
    application.state.settings = active
    application.state.chat_service = ChatService()
    register_exception_handlers(application)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(active.cors_origins),
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    application.include_router(health_router, prefix="/api")
    application.include_router(document_router, prefix="/api")
    application.include_router(task_router, prefix="/api")
    application.include_router(chat_router, prefix="/api")
    logger.debug("RAG API ready")
    return application


def __getattr__(name: str) -> FastAPI:
    """uvicorn 读取 main:app 时再连接存储，避免导入模块就建连。"""
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
