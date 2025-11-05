import pytest

from src.clients import gw
from src.mcp import tools_plan_write_ext


@pytest.mark.anyio
async def test_plan_status_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_plan_status(**kwargs):
        assert kwargs == {"user_id": 101, "external_id": "plan:demo"}
        return {
            "status": "published",
            "etag": "etag-5",
            "updated_at": "2024-01-01T00:00:00Z",
        }

    monkeypatch.setattr(gw, "plan_status", fake_plan_status)

    result = await tools_plan_write_ext.call_tool(
        "plan.status",
        {"external_id": "plan:demo"},
        user_id=101,
    )

    assert result == {
        "status": "published",
        "etag": "etag-5",
        "updated_at": "2024-01-01T00:00:00Z",
    }


@pytest.mark.anyio
async def test_plan_status_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_plan_status(**kwargs):
        raise gw.GwBadResponse("not found", status_code=404)

    monkeypatch.setattr(gw, "plan_status", fake_plan_status)

    result = await tools_plan_write_ext.call_tool(
        "plan.status",
        {"external_id": "plan:missing"},
        user_id=111,
    )

    assert result == {"status": "missing"}


@pytest.mark.anyio
async def test_plan_list_with_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_plan_list(**kwargs):
        captured.update(kwargs)
        return {
            "items": [
                {"external_id": "plan:demo", "status": "published"},
                {"external_id": "plan:demo2", "status": "draft"},
            ],
            "next_cursor": "cursor-2",
        }

    monkeypatch.setattr(gw, "plan_list", fake_plan_list)

    result = await tools_plan_write_ext.call_tool(
        "plan.list",
        {
            "athlete_id": "ath-1",
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
            "limit": 2,
            "cursor": "cursor-1",
        },
        user_id=202,
    )

    assert result == {
        "items": [
            {"external_id": "plan:demo", "status": "published"},
            {"external_id": "plan:demo2", "status": "draft"},
        ],
        "next_cursor": "cursor-2",
    }
    assert captured == {
        "user_id": 202,
        "athlete_id": "ath-1",
        "date_from": "2024-01-01",
        "date_to": "2024-01-31",
        "limit": 2,
        "cursor": "cursor-1",
    }


@pytest.mark.anyio
async def test_plan_list_default_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_plan_list(**kwargs):
        assert kwargs["limit"] == 50
        return {"items": [], "next_cursor": None}

    monkeypatch.setattr(gw, "plan_list", fake_plan_list)

    result = await tools_plan_write_ext.call_tool(
        "plan.list",
        {},
        user_id=303,
    )

    assert result == {"items": [], "next_cursor": None}
