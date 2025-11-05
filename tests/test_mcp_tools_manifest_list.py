import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from src.server import app


async def _post_rpc(client: AsyncClient, payload: dict) -> dict:
    response = await client.post("/mcp", json=payload)
    assert response.status_code == 200
    return response.json()


@pytest.mark.anyio
async def test_tools_list_includes_read_tools() -> None:
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
async def test_http_manifest_includes_read_tools() -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/mcp")
            assert response.status_code == 200
            manifest = response.json()

    tools = manifest.get("tools") or []
    names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
    assert "user.summary.fetch" in names
    assert "user.last_training.fetch" in names
