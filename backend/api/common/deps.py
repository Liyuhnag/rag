# -*- coding: utf-8 -*-
"""路由依赖。服务实例挂在 app.state 上。"""

from fastapi import Request

from config import Settings
from services.chat.service import ChatService
from services.retrieval.fusion.backfill.service import ParentBackfiller


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def get_parent_backfiller(request: Request) -> ParentBackfiller:
    return request.app.state.parent_backfiller
