# -*- coding: utf-8 -*-
"""路由依赖。服务实例挂在 app.state 上。"""

from fastapi import Request

from config import Settings
from services.chat_service import ChatService
from services.document import DocumentStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_document_store(request: Request) -> DocumentStore:
    return request.app.state.document_store


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service
