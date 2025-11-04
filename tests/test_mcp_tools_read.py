from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from src.clients import gw
from src.mcp import tools_read
from src.server import app


async def _post_rpc(client: AsyncClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = await client.post("/mcp", json=payload)
    assert response.status_code == 200
    return response.json()


@pytest.mark.anyio
async def test_tools_list_includes_user_tools() -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "list",
                    "method": "tools/list",
                    "params": {},
                },
            )

    tools = data["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert "user.summary.fetch" in names
    assert "user.last_training.fetch" in names


@pytest.mark.anyio
async def test_user_summary_fetch_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = {"ok": True}

    async def fake_get_user_summary(user_id: int) -> Dict[str, Any]:
        assert user_id == 123
        return summary

    monkeypatch.setattr(gw, "get_user_summary", fake_get_user_summary)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "summary",
                    "method": "tools/call",
                    "params": {
                        "name": "user.summary.fetch",
                        "arguments": {"user_id": 123},
                    },
                },
            )

    assert data["result"] == summary


@pytest.mark.anyio
async def test_user_last_training_default_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, Any] = {}
    today = dt.date(2024, 1, 15)

    async def fake_get_trainings(
        *, user_id: int, oldest: dt.date, newest: dt.date
    ) -> List[Dict[str, Any]]:
        captured["user_id"] = user_id
        captured["oldest"] = oldest
        captured["newest"] = newest
        return [{"id": 1, "date": oldest.isoformat()}]

    monkeypatch.setattr(tools_read, "_today", lambda: today)
    monkeypatch.setattr(gw, "get_trainings", fake_get_trainings)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "trainings-default",
                    "method": "tools/call",
                    "params": {
                        "name": "user.last_training.fetch",
                        "arguments": {"user_id": 55},
                    },
                },
            )

    assert captured == {
        "user_id": 55,
        "oldest": today - dt.timedelta(days=14),
        "newest": today,
    }
    assert data["result"] == {
        "items": [{"id": 1, "date": (today - dt.timedelta(days=14)).isoformat()}]
    }


@pytest.mark.anyio
async def test_user_last_training_custom_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_trainings(
        *, user_id: int, oldest: dt.date, newest: dt.date
    ) -> List[Dict[str, Any]]:
        assert user_id == 77
        assert oldest == dt.date(2024, 2, 1)
        assert newest == dt.date(2024, 2, 10)
        return [{"id": 9, "date": "2024-02-03"}]

    monkeypatch.setattr(gw, "get_trainings", fake_get_trainings)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "trainings-dates",
                    "method": "tools/call",
                    "params": {
                        "name": "user.last_training.fetch",
                        "arguments": {
                            "user_id": 77,
                            "oldest": "2024-02-01",
                            "newest": "2024-02-10",
                        },
                    },
                },
            )

    assert data["result"] == {"items": [{"id": 9, "date": "2024-02-03"}]}


@pytest.mark.anyio
async def test_user_last_training_filters_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = dt.date(2024, 3, 20)

    async def fake_get_trainings(
        *, user_id: int, oldest: dt.date, newest: dt.date
    ) -> List[Dict[str, Any]]:
        assert newest == today
        return [
            {"id": 1, "date": "2024-03-19"},
            {"id": 2, "start_date": "2024-03-25"},
        ]

    monkeypatch.setattr(tools_read, "_today", lambda: today)
    monkeypatch.setattr(gw, "get_trainings", fake_get_trainings)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "trainings-future",
                    "method": "tools/call",
                    "params": {
                        "name": "user.last_training.fetch",
                        "arguments": {"user_id": 11},
                    },
                },
            )

    assert data["result"] == {"items": [{"id": 1, "date": "2024-03-19"}]}


@pytest.mark.anyio
async def test_missing_user_id_returns_invalid_params() -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "err-missing",
                    "method": "tools/call",
                    "params": {"name": "user.summary.fetch", "arguments": {}},
                },
            )

    assert data["error"]["code"] == "InvalidParams"
    assert "user_id" in data["error"]["message"]


@pytest.mark.anyio
async def test_invalid_date_format_returns_invalid_params() -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "err-date",
                    "method": "tools/call",
                    "params": {
                        "name": "user.last_training.fetch",
                        "arguments": {"user_id": 7, "oldest": "2024-13-01"},
                    },
                },
            )

    assert data["error"]["code"] == "InvalidParams"


@pytest.mark.anyio
async def test_gateway_unavailable_maps_to_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_user_summary(user_id: int) -> Dict[str, Any]:
        raise gw.GwUnavailable("boom")

    monkeypatch.setattr(gw, "get_user_summary", fake_get_user_summary)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "err-gw-unavailable",
                    "method": "tools/call",
                    "params": {
                        "name": "user.summary.fetch",
                        "arguments": {"user_id": 8},
                    },
                },
            )

    assert data["error"]["code"] == "GwUnavailable"


@pytest.mark.anyio
async def test_gateway_bad_response_includes_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_trainings(
        *, user_id: int, oldest: dt.date, newest: dt.date
    ) -> List[Dict[str, Any]]:
        raise gw.GwBadResponse("nope", status_code=409)

    monkeypatch.setattr(gw, "get_trainings", fake_get_trainings)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "err-gw-bad",
                    "method": "tools/call",
                    "params": {
                        "name": "user.last_training.fetch",
                        "arguments": {"user_id": 9},
                    },
                },
            )

    assert data["error"]["code"] == "GwBadResponse"
    assert data["error"].get("data") == {"status": 409}
