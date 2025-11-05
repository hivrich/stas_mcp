from __future__ import annotations

from datetime import date, timedelta
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
        self.status_sequence: List[int] = []

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
        if self.status_sequence:
            status = self.status_sequence.pop(0)
            return httpx.Response(status, request=httpx.Request(method, url))
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
            if "external_id" in params:
                return [
                    {
                        "external_id": params["external_id"],
                        "status": "published",
                        "updated_at": "2024-01-02T00:00:00Z",
                        "payload": {"key": "value"},
                        "athlete_id": "ath-1",
                    }
                ]
            return [
                {
                    "external_id": "plan:other",
                    "status": "published",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "payload": {"key": "older"},
                    "athlete_id": "ath-2",
                },
                {
                    "external_id": "plan:demo",
                    "status": "published",
                    "updated_at": "2024-01-02T00:00:00Z",
                    "payload": {"key": "value"},
                    "athlete_id": "ath-1",
                },
                {
                    "external_id": "note:skip",
                    "status": "draft",
                    "updated_at": "2024-01-03T00:00:00Z",
                    "payload": {},
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
    assert isinstance(events, list)
    assert events[0]["external_id"] == "plan:other"


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
    assert len(client.calls) == 1
    call = client.calls[-1]
    assert call["url"] == "/icu/events"
    assert call["params"]["category"] == "WORKOUT"
    assert call["params"]["external_id"] == "plan:demo"
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
async def test_plan_status_window_lookup_when_exact_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, str]] = []

    async def fake_request_json(
        method: str,
        path: str,
        *,
        user_id: int,
        params: Dict[str, Any],
        json_payload: Dict[str, Any] | None = None,
        extra_headers: Dict[str, str] | None = None,
    ) -> Any:
        calls.append(dict(params))
        if "external_id" in params:
            raise gw.GwBadResponse("unsupported", status_code=400)
        return [
            {
                "external_id": "plan:2024-05-01:demo",
                "payload": {"key": "value"},
                "updated_at": "2024-05-01T10:00:00Z",
            }
        ]

    monkeypatch.setattr(gw, "_request_json", fake_request_json)

    result = await gw.plan_status(user_id=9, external_id="plan:2024-05-01:demo")

    assert calls[0] == {"category": "WORKOUT", "external_id": "plan:2024-05-01:demo"}
    assert calls[1]["oldest"] == "2024-05-01"
    assert calls[1]["newest"] == "2024-05-01"
    assert calls[1]["category"] == "WORKOUT"
    expected_etag = hashlib.sha256(b'{"key":"value"}').hexdigest()
    assert result == {
        "status": "published",
        "etag": expected_etag,
        "updated_at": "2024-05-01T10:00:00Z",
    }


@pytest.mark.anyio
async def test_plan_list_filters_and_paginates(
    client_factory: List[DummyAsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_today = date(2024, 3, 1)

    class FakeDate(date):
        @classmethod
        def today(cls) -> date:  # type: ignore[override]
            return fake_today

    monkeypatch.setattr(gw, "date", FakeDate)

    result = await gw.plan_list(user_id=9, limit=1)
    client = client_factory[-1]
    call = client.calls[-1]
    assert call["url"] == "/icu/events"
    assert call["params"]["oldest"] == (fake_today - timedelta(days=90)).isoformat()
    assert call["params"]["newest"] == (fake_today + timedelta(days=7)).isoformat()
    assert call["params"]["category"] == "WORKOUT"
    assert result["items"][0]["external_id"] == "plan:demo"
    assert result["next_cursor"] == "1"


@pytest.mark.anyio
async def test_request_retries_on_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []

    responses = [
        httpx.Response(503, request=httpx.Request("GET", "/icu/events")),
        httpx.Response(200, json=[], request=httpx.Request("GET", "/icu/events")),
    ]

    class SequenceClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        async def __aenter__(self) -> "SequenceClient":
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
            attempts.append(responses[0].status_code)
            return responses.pop(0)

    monkeypatch.setattr(gw.httpx, "AsyncClient", SequenceClient)

    result = await gw._request_json("GET", "/icu/events", user_id=11)

    assert result == []
    assert attempts == [503, 200]
