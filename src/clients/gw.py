"""Async client for STAS Gateway."""

from __future__ import annotations

import asyncio
import json
from base64 import urlsafe_b64encode
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

import httpx

from src.config import settings


class GwError(RuntimeError):
    """Base error for gateway client failures."""


class GwUnavailable(GwError):
    """Raised when the gateway cannot be reached."""


class GwBadResponse(GwError):
    """Raised when the gateway returns unexpected data."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


_REQUEST_TIMEOUT = httpx.Timeout(5.0, connect=2.0)
_RETRY_ATTEMPTS = 2
_RETRY_DELAY = 0.1


def make_bearer_for_user(user_id: int) -> str:
    """Return Authorization header value for a STAS user."""
    payload = json.dumps({"uid": int(user_id)}, separators=(",", ":")).encode("utf-8")
    token = urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"Bearer t_{token}"


async def get_user_summary(user_id: int) -> Dict[str, Any]:
    """Fetch the read-only user summary from the gateway."""
    data = await _request_json(
        "GET",
        "/api/db/user_summary",
        user_id=user_id,
    )
    if not isinstance(data, dict):
        raise GwBadResponse("user summary must be an object")
    return data


async def get_trainings(
    user_id: int,
    oldest: Optional[date] = None,
    newest: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Fetch trainings for the user within the provided dates.

    If dates are not provided, the latest 14 days are returned. Trainings in the
    future are filtered out.
    """

    if newest is None:
        newest = date.today()
    if oldest is None:
        oldest = newest - timedelta(days=14)

    data = await _request_json(
        "GET",
        "/trainings",
        user_id=user_id,
        params={
            "oldest": oldest.isoformat(),
            "newest": newest.isoformat(),
        },
    )

    trainings = _ensure_list_of_dicts(data, "trainings")
    return [item for item in trainings if not _is_future_training(item, newest)]


async def get_plan_week(
    user_id: int,
    oldest: date,
    newest: date,
    category: str = "WORKOUT",
) -> List[Dict[str, Any]]:
    """Fetch plan events for the user in the provided window."""
    params = {
        "oldest": oldest.isoformat(),
        "newest": newest.isoformat(),
        "category": category,
    }
    data = await _request_json(
        "GET",
        "/icu/events",
        user_id=user_id,
        params=params,
    )
    return _ensure_list_of_dicts(data, "plan events")


async def plan_update(
    *,
    user_id: int,
    external_id: str,
    patch: Dict[str, Any],
    dry_run: bool = False,
    if_match: str | None = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"external_id": external_id}
    if dry_run:
        params["dry_run"] = "true"
    headers: Dict[str, str] = {}
    if if_match is not None:
        headers["If-Match"] = if_match
    return await _request_json(
        "PATCH",
        f"/icu/plan/{external_id}",
        user_id=user_id,
        params=params,
        json_payload={"patch": patch},
        extra_headers=headers or None,
    )


async def plan_status(*, user_id: int, external_id: str) -> Dict[str, Any]:
    params = {"external_id": external_id}
    return await _request_json(
        "GET",
        f"/icu/plan/{external_id}",
        user_id=user_id,
        params=params,
    )


async def plan_list(
    *,
    user_id: int,
    athlete_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"limit": str(limit)}
    if athlete_id:
        params["athlete_id"] = athlete_id
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if cursor:
        params["cursor"] = cursor
    return await _request_json(
        "GET",
        "/icu/plan",
        user_id=user_id,
        params=params,
    )


async def _request_json(
    method: str,
    path: str,
    *,
    user_id: int,
    params: Optional[Dict[str, Any]] = None,
    json_payload: Optional[Dict[str, Any]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Any:
    headers = {"Authorization": make_bearer_for_user(user_id)}
    if extra_headers:
        headers.update(extra_headers)
    url = path
    last_error: Optional[BaseException] = None

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            async with httpx.AsyncClient(
                base_url=settings.BRIDGE_BASE,
                timeout=_REQUEST_TIMEOUT,
            ) as client:
                request_kwargs: Dict[str, Any] = {
                    "headers": headers,
                    "params": {"user_id": user_id, **(params or {})},
                }
                if json_payload is not None:
                    request_kwargs["json"] = json_payload
                response = await client.request(
                    method,
                    url,
                    **request_kwargs,
                )
        except httpx.RequestError as exc:  # pragma: no cover - covered via branch
            last_error = exc
            if attempt + 1 >= _RETRY_ATTEMPTS:
                raise GwUnavailable("gateway is unavailable") from exc
            await asyncio.sleep(_RETRY_DELAY)
            continue

        if response.status_code >= 500:
            raise GwUnavailable("gateway returned a server error")
        if response.status_code >= 400:
            error_payload: Any | None = None
            try:
                error_payload = response.json()
            except ValueError:
                try:
                    error_payload = response.text
                except Exception:  # pragma: no cover - defensive
                    error_payload = None
            raise GwBadResponse(
                f"gateway responded with {response.status_code}",
                status_code=response.status_code,
                payload=error_payload,
            )

        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover
            raise GwBadResponse("invalid JSON from gateway") from exc

    raise GwUnavailable("gateway request failed") from last_error


def _ensure_list_of_dicts(data: Any, name: str) -> List[Dict[str, Any]]:
    if not isinstance(data, Iterable):
        raise GwBadResponse(f"{name} must be a list")
    result: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise GwBadResponse(f"{name} entries must be objects")
        result.append(item)
    return result


def _is_future_training(training: Dict[str, Any], newest: date) -> bool:
    training_date = _extract_date(training)
    return training_date is not None and training_date > newest


def _extract_date(training: Dict[str, Any]) -> Optional[date]:
    value = (
        training.get("date") or training.get("start_date") or training.get("start_at")
    )
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            try:
                return datetime.fromisoformat(value).date()
            except ValueError:
                return None
    return None


__all__ = [
    "GwBadResponse",
    "GwError",
    "GwUnavailable",
    "get_plan_week",
    "get_trainings",
    "get_user_summary",
    "make_bearer_for_user",
    "plan_list",
    "plan_status",
    "plan_update",
]
