from __future__ import annotations

from typing import Any, Dict

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from src.clients import gw
from src.server import app


@pytest.mark.anyio
async def test_read_user_summary_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    response_payload = {"ok": True, "name": "Test"}

    async def fake_get_user_summary(user_id: int) -> Dict[str, Any]:
        assert user_id == 42
        return response_payload

    monkeypatch.setattr(gw, "get_user_summary", fake_get_user_summary)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/user/summary", params={"user_id": 42})

    assert response.status_code == 200
    assert response.json() == response_payload


@pytest.mark.anyio
async def test_read_user_summary_requires_user_id() -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/user/summary")

    assert response.status_code == 422


@pytest.mark.anyio
async def test_read_user_summary_rejects_non_numeric_user_id() -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/user/summary", params={"user_id": "abc"})

    assert response.status_code == 422


@pytest.mark.anyio
async def test_read_user_summary_gateway_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_user_summary(user_id: int) -> Dict[str, Any]:
        raise gw.GwUnavailable("boom")

    monkeypatch.setattr(gw, "get_user_summary", fake_get_user_summary)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/user/summary", params={"user_id": 7})

    assert response.status_code == 503
    assert response.json() == {"error": "GwUnavailable"}


@pytest.mark.anyio
async def test_read_user_summary_gateway_bad_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_user_summary(user_id: int) -> Dict[str, Any]:
        raise gw.GwBadResponse("bad", status_code=418)

    monkeypatch.setattr(gw, "get_user_summary", fake_get_user_summary)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/user/summary", params={"user_id": 9})

    assert response.status_code == 502
    assert response.json() == {"error": "GwBadResponse", "status": 418}
