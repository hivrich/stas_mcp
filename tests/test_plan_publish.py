import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from src.server import app


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_plan_publish_confirm_true_uses_real_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_gw(
        method: str,
        path: str,
        *,
        uid: str,
        params: dict[str, object] | None = None,
        json_payload: dict[str, object] | None = None,
        ua: str,
    ) -> dict[str, object]:
        call = {
            "method": method,
            "path": path,
            "params": params or {},
            "json": json_payload or {},
        }
        calls.append(call)
        return {"updated": True, "count": 2}

    monkeypatch.setattr("src.server._resolve_user_id", lambda conn_id: "777")
    monkeypatch.setattr("src.server.gw", fake_gw)
    monkeypatch.setattr("src.server._append_audit", lambda entry: None)

    payload = {
        "external_id": "week-42",
        "confirm": True,
        "draft": {
            "days": [
                {
                    "date": "2025-01-01",
                    "title": "EZ",
                    "blocks": [],
                }
            ]
        },
        "connection_id": "conn-1",
    }

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/mcp/tool/plan.publish", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "published"
    assert data["external_id"] == "week-42"
    assert data["external_id_normalized"] == "plan:week-42"
    assert data["count"] == 2
    assert calls, "gateway must be invoked"
    last_call = calls[-1]
    assert last_call["params"]["dry_run"] == "false"
    assert last_call["json"]["external_id"] == "plan:week-42"
