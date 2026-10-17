# -*- coding: utf-8 -*-
"""对话接口。生成能力尚未接入，成功响应的结构先固定下来。"""

from fastapi import APIRouter
from fastapi import Depends

from api.chat.schemas import ChatRequest
from api.chat.schemas import ChatResponse
from api.common.deps import get_chat_service
from api.common.schemas import ErrorResponse
from services.chat_service import ChatService

router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        422: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
    },
)
def ask(
    payload: ChatRequest,
    chat_service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    return chat_service.answer(payload.question, payload.conversation_id)
