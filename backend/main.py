# -*- coding: utf-8 -*-
"""应用入口。HTTP 服务和 Celery 都从这里启动。"""

import logging
from collections.abc import Callable

from celery import Celery
from celery.signals import worker_process_init
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import Settings
from config import load_settings

logger = logging.getLogger("rag.api")


def _result_backend(broker: str) -> str:
    if broker.endswith("/0"):
        return f"{broker[:-1]}1"
    return broker


_settings = load_settings()
celery_app = Celery(
    "rag",
    broker=_settings.redis_url,
    backend=_result_backend(_settings.redis_url),
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=4,
    worker_send_task_events=True,
    task_send_sent_event=True,
    broker_connection_retry_on_startup=True,
)


@worker_process_init.connect
def _open_storage(**_kwargs: object) -> None:
    """每个 worker 进程自己连接数据库和对象存储。"""
    from services.common.connect import connect_storage

    connect_storage(load_settings())


from api.chat import router as chat_router
from api.common.errors import register_exception_handlers
from api.documents import router as document_router
from api.health import router as health_router
from api.tasks import router as task_router
from services.chat.service import ChatService
from services.common.connect import connect_storage


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
