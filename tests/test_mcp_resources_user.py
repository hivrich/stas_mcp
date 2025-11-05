from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterator, List

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from src.clients import gw
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
async def test_resources_list_contains_user_entries() -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "resources-list",
                    "method": "resources/list",
                    "params": {},
                },
            )

    resources = data["result"]["resources"]
    uris = {resource["uri"] for resource in resources}
    assert "user.summary.json" in uris
    assert "user.last_training.json" in uris


@pytest.mark.anyio
async def test_read_summary_requires_user_id() -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "resources-read-summary-missing",
                    "method": "resources/read",
                    "params": {"uri": "user.summary.json"},
                },
            )

    assert data["error"]["code"] == "UserIdRequired"
    assert "session.set_user_id" in data["error"]["message"]


@pytest.mark.anyio
async def test_set_user_id_then_read_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_user_summary(user_id: int) -> Dict[str, Any]:
        assert user_id == 111
        return {"user_id": user_id, "name": "Test User"}

    monkeypatch.setattr(gw, "get_user_summary", fake_get_user_summary)

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
                        "arguments": {"user_id": 111},
                    },
                },
            )

            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "read-summary",
                    "method": "resources/read",
                    "params": {"uri": "user.summary.json"},
                },
            )

    contents = data["result"]["contents"]
    assert len(contents) == 1
    payload = contents[0]["data"]
    assert payload == {"user_id": 111, "name": "Test User"}


@pytest.mark.anyio
async def test_read_last_training_filters_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, dt.date] = {}

    async def fake_get_trainings(
        *, user_id: int, oldest: dt.date, newest: dt.date
    ) -> List[Dict[str, Any]]:
        assert user_id == 222
        captured["oldest"] = oldest
        captured["newest"] = newest
        return [
            {"id": 1, "date": (newest - dt.timedelta(days=1)).isoformat()},
            {"id": 2, "start_date": (newest + dt.timedelta(days=3)).isoformat()},
            {"id": 3, "start_at": f"{newest.isoformat()}T10:00:00"},
        ]

    monkeypatch.setattr(gw, "get_trainings", fake_get_trainings)

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
                        "arguments": {"user_id": 222},
                    },
                },
            )

            data = await _post_rpc(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": "read-trainings",
                    "method": "resources/read",
                    "params": {"uri": "user.last_training.json"},
                },
            )

    contents = data["result"]["contents"]
    items = contents[0]["data"]["items"]
    ids = {item["id"] for item in items}
    assert ids == {1, 3}
    assert captured["newest"] - captured["oldest"] == dt.timedelta(days=14)
