from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

import hashlib

import httpx
import pytest

from src.clients import gw


class DummyAsyncClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.calls: List[Dict[str, Any]] = []

    async def __aenter__(self) -> "DummyAsyncClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        params: Dict[str, Any],
        json: Any | None = None,
    ) -> httpx.Response:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "json": json,
            }
        )
        return httpx.Response(
            200,
            json=self._build_payload(url, method, params),
            request=httpx.Request(method, url),
        )

    def _build_payload(self, url: str, method: str, params: Dict[str, Any]) -> Any:
        if url.endswith("/api/db/user_summary"):
            return {"ok": True}
        if url.endswith("/trainings"):
            return [
                {"date": "2024-01-10", "title": "past"},
                {"date": "2024-01-20", "title": "future"},
            ]
        if url.endswith("/icu/events") and method == "POST":
            return {"updated": True, "count": 2, "etag": "etag-123"}
        if url.endswith("/icu/events"):
            if (
                params.get("oldest") == "2024-01-01"
                and params.get("newest") == "2024-01-07"
            ):
                return [
                    {"date": "2024-01-10", "title": "plan"},
                ]
            return [
                {
                    "external_id": "plan:demo",
                    "status": "published",
                    "updated_at": "2024-01-02T00:00:00Z",
                    "payload": {"key": "value"},
                },
                {
                    "external_id": "plan:other",
                    "status": "published",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "payload": {"key": "older"},
                },
            ]
        return [
            {"date": "2024-01-10", "title": "plan"},
        ]


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def client_factory(monkeypatch: pytest.MonkeyPatch) -> List[DummyAsyncClient]:
    created: List[DummyAsyncClient] = []

    def factory(*args: Any, **kwargs: Any) -> DummyAsyncClient:
        client = DummyAsyncClient(*args, **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(gw.httpx, "AsyncClient", factory)
    return created


@pytest.mark.anyio
async def test_get_user_summary_sends_authorization(
    client_factory: List[DummyAsyncClient],
) -> None:
    data = await gw.get_user_summary(42)
    assert data == {"ok": True}
    client = client_factory[-1]
    call = client.calls[0]
    assert call["headers"]["Authorization"].startswith("Bearer t_")
    assert call["params"]["user_id"] == 42
    assert call["url"] == "/api/db/user_summary"
    assert client.kwargs["base_url"] == gw.settings.BRIDGE_BASE


@pytest.mark.anyio
async def test_get_trainings_defaults_to_last_14_days(
    monkeypatch: pytest.MonkeyPatch,
    client_factory: List[DummyAsyncClient],
) -> None:
    fake_today = date(2024, 1, 15)

    class FakeDate(date):
        @classmethod
        def today(cls) -> date:  # type: ignore[override]
            return fake_today

    monkeypatch.setattr(gw, "date", FakeDate)

    trainings = await gw.get_trainings(7)
    assert trainings == [{"date": "2024-01-10", "title": "past"}]
    client = client_factory[-1]
    call = client.calls[0]
    assert call["params"]["oldest"] == "2024-01-01"
    assert call["params"]["newest"] == "2024-01-15"


@pytest.mark.anyio
async def test_get_plan_week_includes_category(
    client_factory: List[DummyAsyncClient],
) -> None:
    events = await gw.get_plan_week(7, date(2024, 1, 1), date(2024, 1, 7))
    client = client_factory[-1]
    call = client.calls[-1]
    assert call["params"]["category"] == "WORKOUT"
    assert events == [{"date": "2024-01-10", "title": "plan"}]


@pytest.mark.anyio
async def test_plan_update_posts_events_endpoint(
    client_factory: List[DummyAsyncClient],
) -> None:
    response = await gw.plan_update(
        user_id=5,
        external_id="demo",
        patch={"days": []},
        dry_run=False,
    )
    client = client_factory[-1]
    call = client.calls[-1]
    assert call["method"] == "POST"
    assert call["url"] == "/icu/events"
    assert call["params"]["dry_run"] == "false"
    assert call["json"]["external_id"] == "plan:demo"
    assert response == {"updated": True, "count": 2, "etag": "etag-123"}


@pytest.mark.anyio
async def test_plan_status_reads_events(client_factory: List[DummyAsyncClient]) -> None:
    result = await gw.plan_status(user_id=6, external_id="demo")
    client = client_factory[-1]
    call = client.calls[-1]
    assert call["url"] == "/icu/events"
    assert call["params"]["category"] == "WORKOUT"
    expected_etag = hashlib.sha256(b'{"key":"value"}').hexdigest()
    assert result["status"] == "published"
    assert result["etag"] == expected_etag
    assert result["updated_at"] == "2024-01-02T00:00:00Z"


@pytest.mark.anyio
async def test_plan_status_missing_when_not_found(
    client_factory: List[DummyAsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request_json(*args: Any, **kwargs: Any) -> Any:
        return []

    monkeypatch.setattr(gw, "_request_json", fake_request_json)
    result = await gw.plan_status(user_id=7, external_id="plan:absent")
    assert result == {"status": "missing"}


@pytest.mark.anyio
async def test_plan_list_filters_and_paginates(
    client_factory: List[DummyAsyncClient],
) -> None:
    result = await gw.plan_list(user_id=9, limit=1)
    client = client_factory[-1]
    call = client.calls[-1]
    assert call["url"] == "/icu/events"
    assert result["items"][0]["external_id"] == "plan:demo"
    assert result["next_cursor"] == "1"
