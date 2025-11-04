from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from linking import get_status, reset, set_linked  # noqa: E402
from server import app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    reset()
    yield
    reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_link_endpoint_requires_connection_id(client: TestClient) -> None:
    response = client.get("/_link")
    assert response.status_code == 422


def test_link_endpoint_sets_pending_state(client: TestClient) -> None:
    connection_id = "dev-test-123"
    response = client.get("/_link", params={"connection_id": connection_id})
    assert response.status_code == 200
    payload = response.json()
    assert payload["connection_id"] == connection_id
    assert payload["linked"] is False
    assert get_status(connection_id) == {"linked": False}


def test_whoami_unknown_connection(client: TestClient) -> None:
    response = client.get("/_whoami", params={"connection_id": "missing-001"})
    assert response.status_code == 200
    payload = response.json()
    assert payload == {"connection_id": "missing-001", "linked": False}


def test_whoami_after_linked(client: TestClient) -> None:
    connection_id = "conn-456"
    set_linked(connection_id, 99)
    response = client.get("/_whoami", params={"connection_id": connection_id})
    assert response.status_code == 200
    payload = response.json()
    assert payload["connection_id"] == connection_id
    assert payload["linked"] is True
    assert payload["user_id"] == 99
