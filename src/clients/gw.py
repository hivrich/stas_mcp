"""Async client for STAS Gateway."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from base64 import urlsafe_b64encode
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

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
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = (0.2, 0.5, 1.0)


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
    normalized = _normalize_plan_external_id(external_id)
    params: Dict[str, Any] = {
        "external_id_prefix": "plan:",
        "dry_run": "true" if dry_run else "false",
    }
    headers: Dict[str, str] = {}
    if if_match is not None:
        headers["If-Match"] = if_match
    payload = {"external_id": normalized, "patch": patch}
    return await _request_json(
        "POST",
        "/icu/events",
        user_id=user_id,
        params=params,
        json_payload=payload,
        extra_headers=headers or None,
    )


async def plan_status(*, user_id: int, external_id: str) -> Dict[str, Any]:
    normalized = _normalize_plan_external_id(external_id)
    params = {"category": "WORKOUT", "external_id": normalized}
    need_window_lookup = False

    try:
        data = await _request_json(
            "GET",
            "/icu/events",
            user_id=user_id,
            params=params,
        )
    except GwBadResponse as exc:
        if exc.status_code == 404:
            return {"status": "missing"}
        if exc.status_code and exc.status_code < 500:
            need_window_lookup = True
        else:
            raise
    else:
        events = _ensure_list_of_dicts(data, "plan events")
        for event in events:
            if str(event.get("external_id")) != normalized:
                continue
            result: Dict[str, Any] = {"status": "published"}
            if etag := _hash_event_payload(event):
                result["etag"] = etag
            if updated := _event_updated_at(event):
                result["updated_at"] = updated
            return result
        if not need_window_lookup:
            return {"status": "missing"}

    window = _status_window(normalized)
    params = {
        "oldest": window["oldest"],
        "newest": window["newest"],
        "category": "WORKOUT",
    }
    try:
        data = await _request_json(
            "GET",
            "/icu/events",
            user_id=user_id,
            params=params,
        )
    except GwBadResponse as exc:
        if exc.status_code == 404:
            return {"status": "missing"}
        raise

    events = _ensure_list_of_dicts(data, "plan events")
    for event in events:
        if str(event.get("external_id")) != normalized:
            continue
        result = {"status": "published"}
        if etag := _hash_event_payload(event):
            result["etag"] = etag
        if updated := _event_updated_at(event):
            result["updated_at"] = updated
        return result
    return {"status": "missing"}


async def plan_list(
    *,
    user_id: int,
    athlete_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Dict[str, Any]:
    today = date.today()
    oldest = today - timedelta(days=90)
    newest = today + timedelta(days=7)
    params = {
        "oldest": (date_from or oldest.isoformat()),
        "newest": (date_to or newest.isoformat()),
        "category": "WORKOUT",
    }

    data = await _request_json(
        "GET",
        "/icu/events",
        user_id=user_id,
        params=params,
    )
    events = _ensure_list_of_dicts(data, "plan events")

    filtered = [
        _summarize_plan_event(event)
        for event in events
        if isinstance(event.get("external_id"), str)
        and str(event["external_id"]).startswith("plan:")
    ]

    if athlete_id:
        filtered = [item for item in filtered if item.get("athlete_id") == athlete_id]

    filtered.sort(key=lambda item: item.get("updated_at_sort"), reverse=True)

    start_index = _decode_cursor(cursor)
    end_index = start_index + max(0, int(limit))
    page = filtered[start_index:end_index]
    next_cursor: str | None = None
    if end_index < len(filtered):
        next_cursor = str(end_index)

    items = [{k: v for k, v in item.items() if k != "updated_at_sort"} for item in page]

    return {"items": items, "next_cursor": next_cursor}


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
            await asyncio.sleep(_retry_delay_for_attempt(attempt))
            continue

        if response.status_code >= 500:
            if attempt + 1 >= _RETRY_ATTEMPTS:
                raise GwUnavailable("gateway returned a server error")
            await asyncio.sleep(_retry_delay_for_attempt(attempt))
            continue
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


def _retry_delay_for_attempt(attempt: int) -> float:
    index = min(attempt, len(_RETRY_BACKOFF) - 1)
    return _RETRY_BACKOFF[index]


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


def _normalize_plan_external_id(external_id: str) -> str:
    if external_id.startswith("plan:"):
        return external_id
    return f"plan:{external_id}" if external_id else "plan:auto"


def _status_window(external_id: str) -> Dict[str, str]:
    today = date.today()
    match = re.match(r"^plan:(\d{4}-\d{2}-\d{2})(?::.*)?$", external_id)
    if match:
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError:
            pass
        else:
            iso = day.isoformat()
            return {"oldest": iso, "newest": iso}
    oldest = (today - timedelta(days=90)).isoformat()
    newest = (today + timedelta(days=7)).isoformat()
    return {"oldest": oldest, "newest": newest}


def _hash_event_payload(event: Mapping[str, Any]) -> str | None:
    payload = event.get("payload")
    if isinstance(payload, (dict, list)):
        try:
            serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return None
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return None


def _event_updated_at(event: Mapping[str, Any]) -> str | None:
    for key in ("updated_at", "modified_at", "created_at", "start_date_local"):
        value = event.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
    return None


def _summarize_plan_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "external_id": event.get("external_id"),
        "status": event.get("status", "published"),
    }
    if "athlete_id" in event:
        summary["athlete_id"] = event.get("athlete_id")
    if etag := _hash_event_payload(event):
        summary["etag"] = etag
    updated_at = _event_updated_at(event)
    if updated_at:
        summary["updated_at"] = updated_at
        summary["updated_at_sort"] = updated_at
    else:
        summary["updated_at_sort"] = ""
    return summary


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = int(cursor)
    except (TypeError, ValueError):
        return 0
    return max(value, 0)


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
