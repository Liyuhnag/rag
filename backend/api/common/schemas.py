# -*- coding: utf-8 -*-
"""跨接口共用的响应结构。"""

from pydantic import BaseModel
from pydantic import ConfigDict


def to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class APIModel(BaseModel):
    """对外 JSON 使用驼峰字段。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class Pagination(APIModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int


class ErrorBody(APIModel):
    code: str
    message: str
    details: object | None = None


class ErrorResponse(APIModel):
    error: ErrorBody


def error_payload(
    code: str,
    message: str,
    details: object | None = None,
) -> dict[str, object]:
    body = ErrorBody(code=code, message=message, details=details)
    response = ErrorResponse(error=body)
    return response.model_dump(by_alias=True, exclude_none=True)


def paginate(page: int, page_size: int, total_items: int) -> Pagination:
    """把页码和总数收成统一的分页对象。"""
    if total_items:
        total_pages = (total_items + page_size - 1) // page_size
    else:
        total_pages = 0
    return Pagination(
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
    )
