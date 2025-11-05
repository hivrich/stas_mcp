"""MCP resource handlers for user summary and training data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from src.clients import gw
from src.session import store as session_store


@dataclass(slots=True)
class ResourceDefinition:
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


class ResourceError(RuntimeError):
    def __init__(self, code: str, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


_RESOURCES = {
    definition.uri: definition
    for definition in (
        ResourceDefinition(
            uri="user.summary.json",
            name="user.summary.json",
            description="Read-only summary for the stored user_id.",
        ),
        ResourceDefinition(
            uri="user.last_training.json",
            name="user.last_training.json",
            description="Recent trainings (last 14 days) for the stored user_id.",
        ),
    )
}


def list_resources() -> List[Dict[str, Any]]:
    return [definition.as_dict() for definition in _RESOURCES.values()]


async def read_resource(uri: str) -> Dict[str, Any]:
    definition = _RESOURCES.get(uri)
    if definition is None:
        raise ResourceError("ResourceNotFound", f"Unknown resource '{uri}'")

    user_id = session_store.get_user_id()
    if user_id is None:
        raise ResourceError(
            "UserIdRequired",
            "user_id is required; call session.set_user_id(user_id)",
        )

    if uri == "user.summary.json":
        try:
            payload = await gw.get_user_summary(user_id)
        except gw.GwUnavailable as exc:
            raise ResourceError("GwUnavailable", "gateway unavailable") from exc
        except gw.GwBadResponse as exc:
            data = {"status": exc.status_code} if exc.status_code is not None else None
            raise ResourceError(
                "GwBadResponse", "gateway returned bad response", data
            ) from exc
        return _json_contents(uri, payload)

    if uri == "user.last_training.json":
        today = date.today()
        oldest = today - timedelta(days=14)
        try:
            items = await gw.get_trainings(user_id=user_id, oldest=oldest, newest=today)
        except gw.GwUnavailable as exc:
            raise ResourceError("GwUnavailable", "gateway unavailable") from exc
        except gw.GwBadResponse as exc:
            data = {"status": exc.status_code} if exc.status_code is not None else None
            raise ResourceError(
                "GwBadResponse", "gateway returned bad response", data
            ) from exc
        filtered = _filter_future_trainings(items, today)
        return _json_contents(uri, {"items": filtered})

    raise ResourceError("ResourceNotFound", f"Unknown resource '{uri}'")


def _json_contents(uri: str, payload: Any) -> Dict[str, Any]:
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "data": payload,
            }
        ]
    }


def _filter_future_trainings(
    trainings: List[Dict[str, Any]], newest: date
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for training in trainings:
        training_date = _extract_training_date(training)
        if training_date is None or training_date <= newest:
            result.append(training)
    return result


def _extract_training_date(training: Dict[str, Any]) -> Optional[date]:
    value = (
        training.get("date") or training.get("start_date") or training.get("start_at")
    )
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        for parser in (date.fromisoformat, _parse_datetime_date):
            try:
                parsed = parser(value)
            except ValueError:
                continue
            if parsed:
                return parsed
        return None
    return None


def _parse_datetime_date(value: str) -> date:
    return datetime.fromisoformat(value).date()


__all__ = ["list_resources", "read_resource", "ResourceError"]
