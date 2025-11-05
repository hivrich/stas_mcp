from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from src.clients import gw
from src.mcp import tools_read
from src.server import app
from src.session import store as session_store


async def _post_rpc(client: AsyncClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = await client.post("/mcp", json=payload)
    assert response.status_code == 200
    return response.json()


@pytest.fixture(autouse=True)
def _reset_session_store() -> Iterator[None]:
    session_store.clear_user_id()
    yield
    session_store.clear_user_id()


@pytest.mark.anyio
async def test_session_set_get_clear_cycle() -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            result = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "set",
                    "method": "tools/call",
                    "params": {
                        "name": "session.set_user_id",
                        "arguments": {"user_id": 99},
                    },
                },
            )
            assert result["result"] == {"ok": True, "user_id": 99}

            result = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "get",
                    "method": "tools/call",
                    "params": {"name": "session.get_user_id", "arguments": {}},
                },
            )
            assert result["result"] == {"user_id": 99}

            result = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "clear",
                    "method": "tools/call",
                    "params": {"name": "session.clear_user_id", "arguments": {}},
                },
            )
            assert result["result"] == {"ok": True}

            result = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "get-after-clear",
                    "method": "tools/call",
                    "params": {"name": "session.get_user_id", "arguments": {}},
                },
            )
            assert result["result"] == {"user_id": None}


@pytest.mark.anyio
async def test_user_tools_fallback_to_session(monkeypatch: pytest.MonkeyPatch) -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "set-session",
                    "method": "tools/call",
                    "params": {
                        "name": "session.set_user_id",
                        "arguments": {"user_id": 555},
                    },
                },
            )

            async def fake_get_user_summary(user_id: int) -> Dict[str, Any]:
                assert user_id == 555
                return {"ok": True, "user_summary": {"text": "Stored"}}

            monkeypatch.setattr(gw, "get_user_summary", fake_get_user_summary)

            result = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "summary-session",
                    "method": "tools/call",
                    "params": {"name": "user.summary.fetch", "arguments": {}},
                },
            )
            assert result["result"] == "Stored"

            today = dt.date(2024, 5, 20)

            async def fake_get_trainings(
                *, user_id: int, oldest: dt.date, newest: dt.date
            ) -> list[dict[str, Any]]:
                assert user_id == 555
                assert oldest == today - dt.timedelta(days=14)
                assert newest == today
                return [{"id": 1, "date": oldest.isoformat()}]

            monkeypatch.setattr(tools_read, "_today", lambda: today)
            monkeypatch.setattr(gw, "get_trainings", fake_get_trainings)

            result = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "trainings-session",
                    "method": "tools/call",
                    "params": {"name": "user.last_training.fetch", "arguments": {}},
                },
            )
            assert result["result"] == {
                "items": [
                    {"id": 1, "date": (today - dt.timedelta(days=14)).isoformat()}
                ]
            }


@pytest.mark.anyio
async def test_user_tools_without_session_error() -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            result = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "summary-no-session",
                    "method": "tools/call",
                    "params": {"name": "user.summary.fetch", "arguments": {}},
                },
            )

    assert result["error"]["code"] == "InvalidParams"
    assert "session.set_user_id" in result["error"]["message"]
