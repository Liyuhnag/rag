# -*- coding: utf-8 -*-
"""健康检查接口。"""


def test_health_returns_ok(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"


def test_health_allows_frontend_origin(client):
    response = client.get(
        "/api/health",
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:5173"
    )


def test_health_allows_devcloud_frontend(client):
    response = client.get(
        "/api/health",
        headers={"Origin": "http://ahanali.devcloud.woa.com:5173"},
    )

    assert response.headers["access-control-allow-origin"] == (
        "http://ahanali.devcloud.woa.com:5173"
    )
