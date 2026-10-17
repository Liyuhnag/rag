# -*- coding: utf-8 -*-
"""对话接口。检索生成尚未接入，接口只固定请求和错误契约。"""


def test_chat_rejects_blank_question(client):
    response = client.post("/api/chat", json={"question": "   "})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"]


def test_chat_returns_not_implemented_for_valid_question(client):
    response = client.post(
        "/api/chat",
        json={"question": "什么是 RAG?", "conversationId": "conv-1"},
    )

    assert response.status_code == 501
    assert response.json() == {
        "error": {
            "code": "NOT_IMPLEMENTED",
            "message": "检索生成尚未接入",
        }
    }


def test_chat_rejects_too_long_question(client):
    response = client.post("/api/chat", json={"question": "问" * 4001})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
