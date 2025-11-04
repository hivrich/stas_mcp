from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from src.clients import gw
from src.routes import read_user
from src.server import app


@pytest.mark.anyio
async def test_read_last_training_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    trainings = [{"date": "2024-01-10", "title": "past"}]

    async def fake_get_trainings(
        user_id: int, *, oldest: date, newest: date
    ) -> List[Dict[str, Any]]:
        assert user_id == 17
        assert oldest == date(2024, 1, 1)
        assert newest == date(2024, 1, 15)
        return trainings

    def fake_today() -> date:
        return date(2024, 1, 15)

    monkeypatch.setattr(gw, "get_trainings", fake_get_trainings)
    monkeypatch.setattr(read_user, "_today", fake_today)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/user/last_training",
                params={"user_id": 17, "oldest": "2024-01-01", "newest": "2024-01-15"},
            )

    assert response.status_code == 200
    assert response.json() == {"items": trainings}


@pytest.mark.anyio
async def test_read_last_training_defaults_to_last_14_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, Any] = {}

    async def fake_get_trainings(
        user_id: int, *, oldest: date, newest: date
    ) -> List[Dict[str, Any]]:
        captured["user_id"] = user_id
        captured["oldest"] = oldest
        captured["newest"] = newest
        return []

    def fake_today() -> date:
        return date(2024, 1, 15)

    monkeypatch.setattr(gw, "get_trainings", fake_get_trainings)
    monkeypatch.setattr(read_user, "_today", fake_today)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/user/last_training", params={"user_id": 21}
            )

    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert captured["user_id"] == 21
    assert captured["oldest"] == date(2024, 1, 1)
    assert captured["newest"] == date(2024, 1, 15)


@pytest.mark.anyio
async def test_read_last_training_invalid_date_format() -> None:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/user/last_training",
                params={"user_id": 3, "oldest": "2024-13-01"},
            )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_read_last_training_oldest_after_newest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_today() -> date:
        return date(2024, 1, 15)

    monkeypatch.setattr(read_user, "_today", fake_today)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/user/last_training",
                params={"user_id": 4, "oldest": "2024-01-10", "newest": "2024-01-01"},
            )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_read_last_training_filters_future_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_trainings(
        user_id: int, *, oldest: date, newest: date
    ) -> List[Dict[str, Any]]:
        return [
            {"date": "2024-01-14", "title": "keep"},
            {"date": "2024-01-16", "title": "drop"},
        ]

    def fake_today() -> date:
        return date(2024, 1, 15)

    monkeypatch.setattr(gw, "get_trainings", fake_get_trainings)
    monkeypatch.setattr(read_user, "_today", fake_today)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/user/last_training",
                params={"user_id": 8, "oldest": "2024-01-01", "newest": "2024-01-20"},
            )

    assert response.status_code == 200
    assert response.json() == {"items": [{"date": "2024-01-14", "title": "keep"}]}
