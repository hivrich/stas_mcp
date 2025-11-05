import pytest

from src.clients import gw
from src.mcp import tools_plan_write_ext


@pytest.mark.anyio
async def test_plan_update_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_plan_update(**kwargs):
        captured.update(kwargs)
        return {"would_change": True, "diff": {"days": ["changed"]}}

    monkeypatch.setattr(gw, "plan_update", fake_plan_update)

    result = await tools_plan_write_ext.call_tool(
        "plan.update",
        {
            "external_id": "plan:2024-w10",
            "patch": {"days": ["changed"]},
        },
        user_id=42,
    )

    assert result == {"would_change": True, "diff": {"days": ["changed"]}}
    assert captured == {
        "user_id": 42,
        "external_id": "plan:2024-w10",
        "patch": {"days": ["changed"]},
        "dry_run": True,
        "if_match": None,
    }


@pytest.mark.anyio
async def test_plan_update_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_plan_update(**kwargs):
        assert kwargs["dry_run"] is False
        assert kwargs["if_match"] == "etag-1"
        return {"updated": True, "etag": "etag-2"}

    monkeypatch.setattr(gw, "plan_update", fake_plan_update)

    result = await tools_plan_write_ext.call_tool(
        "plan.update",
        {
            "external_id": "plan:2024-w11",
            "patch": {"days": []},
            "confirm": True,
            "if_match": "etag-1",
        },
        user_id=77,
    )

    assert result == {"updated": True, "etag": "etag-2"}


@pytest.mark.anyio
async def test_plan_update_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_plan_update(**kwargs):
        assert kwargs["dry_run"] is False
        return {"updated": False}

    monkeypatch.setattr(gw, "plan_update", fake_plan_update)

    result = await tools_plan_write_ext.call_tool(
        "plan.update",
        {
            "external_id": "plan:2024-w12",
            "patch": {"days": []},
            "confirm": True,
        },
        user_id=88,
    )

    assert result == {"updated": False, "etag": None}


@pytest.mark.anyio
async def test_plan_update_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_plan_update(**kwargs):
        raise gw.GwBadResponse(
            "conflict",
            status_code=409,
            payload={"etag_current": "etag-current"},
        )

    monkeypatch.setattr(gw, "plan_update", fake_plan_update)

    with pytest.raises(tools_plan_write_ext.ToolError) as excinfo:
        await tools_plan_write_ext.call_tool(
            "plan.update",
            {
                "external_id": "plan:2024-w13",
                "patch": {"days": []},
                "confirm": True,
            },
            user_id=55,
        )

    err = excinfo.value
    assert err.code == "Conflict"
    assert err.data == {"etag_current": "etag-current"}


@pytest.mark.anyio
async def test_plan_update_gateway_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_plan_update(**kwargs):
        raise gw.GwUnavailable("timeout")

    monkeypatch.setattr(gw, "plan_update", fake_plan_update)

    with pytest.raises(tools_plan_write_ext.ToolError) as excinfo:
        await tools_plan_write_ext.call_tool(
            "plan.update",
            {
                "external_id": "plan:2024-w14",
                "patch": {"days": []},
                "confirm": True,
            },
            user_id=90,
        )

    err = excinfo.value
    assert err.code == "GwUnavailable"
