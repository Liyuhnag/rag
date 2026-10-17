# -*- coding: utf-8 -*-
"""把各类异常收成同一种错误响应。"""

import logging

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from models.common import error_payload
from services.exceptions import FeatureNotReady
from services.exceptions import StorageError

logger = logging.getLogger("rag.api")

_HTTP_CODES = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "FILE_TOO_LARGE",
}


class AppError(Exception):
    """API 边界上可预期的失败。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(
        request: Request,
        exc: AppError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(StorageError)
    async def handle_storage_error(
        request: Request,
        exc: StorageError,
    ) -> JSONResponse:
        logger.error(
            "storage error on %s %s: %s",
            request.method,
            request.url.path,
            exc.message,
        )
        return JSONResponse(
            status_code=502,
            content=error_payload("STORAGE_UNAVAILABLE", exc.message),
        )

    @app.exception_handler(FeatureNotReady)
    async def handle_feature_not_ready(
        request: Request,
        exc: FeatureNotReady,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=501,
            content=error_payload("NOT_IMPLEMENTED", exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        del request
        details = []
        for err in exc.errors():
            loc = err.get("loc", ())
            details.append(
                {
                    "loc": [str(part) for part in loc],
                    "message": str(err.get("msg", "")),
                    "type": str(err.get("type", "")),
                }
            )
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "VALIDATION_ERROR",
                "请求参数不合法",
                details,
            ),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        del request
        if isinstance(exc.detail, str):
            message = exc.detail
        else:
            message = "请求失败"
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, message),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        # 集成边界：内部异常只进日志，响应不带堆栈。
        logger.error(
            "unhandled error on %s %s",
            request.method,
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            content=error_payload("INTERNAL_ERROR", "服务器内部错误"),
        )
