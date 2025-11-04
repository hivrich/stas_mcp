from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

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
    ) -> httpx.Response:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
            }
        )
        return httpx.Response(
            200, json=self._build_payload(url), request=httpx.Request(method, url)
        )

    def _build_payload(self, url: str) -> Any:
        if url.endswith("/api/db/user_summary"):
            return {"ok": True}
        if url.endswith("/trainings"):
            return [
                {"date": "2024-01-10", "title": "past"},
                {"date": "2024-01-20", "title": "future"},
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
