# -*- coding: utf-8 -*-
"""FastAPI 应用入口。"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.chat import router as chat_router
from api.common.errors import register_exception_handlers
from api.documents import router as document_router
from api.health import router as health_router
from config import Settings
from config import load_settings
from services.chat_service import ChatService
from services.document import build_document_store

logger = logging.getLogger("rag.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    active = load_settings() if settings is None else settings
    application = FastAPI(title="RAG API", version="0.1.0")
    application.state.settings = active
    application.state.document_store = build_document_store(active)
    application.state.chat_service = ChatService()
    register_exception_handlers(application)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(active.cors_origins),
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
    application.include_router(chat_router, prefix="/api")
    logger.debug(
        "RAG API ready, storage %s",
        active.storage_backend,
    )
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
