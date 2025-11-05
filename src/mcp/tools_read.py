from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from typing import Any, Dict, List, Mapping, Optional

from src.clients import gw
from src.session import store as session_store

_DEFAULT_RANGE_DAYS = 14


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.name,
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class ToolError(RuntimeError):
    def __init__(self, code: str, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


_TOOL_DEFINITIONS = (
    ToolDefinition(
        name="user.summary.fetch",
        description="Fetch read-only summary for a user by user_id (returns plain text summary).",
        input_schema={
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
            },
        },
    ),
    ToolDefinition(
        name="user.last_training.fetch",
        description="Fetch the user's trainings within a date window (default last 14 days).",
        input_schema={
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "oldest": {
                    "type": "string",
                    "pattern": r"^\\d{4}-\\d{2}-\\d{2}$",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "newest": {
                    "type": "string",
                    "pattern": r"^\\d{4}-\\d{2}-\\d{2}$",
                    "description": "End date (YYYY-MM-DD)",
                },
            },
        },
    ),
)


_TOOL_HANDLERS = {definition.name: definition for definition in _TOOL_DEFINITIONS}


def get_tool_definitions() -> List[Dict[str, Any]]:
    return [definition.as_dict() for definition in _TOOL_DEFINITIONS]


def has_tool(name: str) -> bool:
    return name in _TOOL_HANDLERS


async def call_tool(name: str, arguments: Mapping[str, Any]) -> Any:
    if name == "user.summary.fetch":
        return await _call_user_summary(arguments)
    if name == "user.last_training.fetch":
        return await _call_user_last_training(arguments)
    raise ToolError("InvalidParams", f"Unknown tool '{name}'")


def _normalize_user_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ToolError("InvalidParams", "user_id must be an integer")
    try:
        user_id = int(value)
    except (TypeError, ValueError):
        raise ToolError("InvalidParams", "user_id must be an integer") from None
    if user_id < 0:
        raise ToolError("InvalidParams", "user_id must be non-negative")
    return user_id


def _coerce_user_id(arguments: Mapping[str, Any]) -> int:
    if "user_id" in arguments:
        return _normalize_user_id(arguments["user_id"])

    stored = session_store.get_user_id()
    if stored is None:
        raise ToolError(
            "InvalidParams",
            "user_id is required; call session.set_user_id(user_id) first or pass user_id",
        )
    return _normalize_user_id(stored)


def _parse_date(arguments: Mapping[str, Any], key: str) -> Optional[date]:
    if key not in arguments:
        return None
    value = arguments[key]
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ToolError("InvalidParams", f"{key} must be a YYYY-MM-DD string")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ToolError(
                "InvalidParams", f"{key} must be in YYYY-MM-DD format"
            ) from None
    raise ToolError("InvalidParams", f"{key} must be a YYYY-MM-DD string")


def _today() -> date:
    return date.today()


async def _call_user_summary(arguments: Mapping[str, Any]) -> str:
    user_id = _coerce_user_id(arguments)
    try:
        raw_summary = await gw.get_user_summary(user_id)
    except gw.GwUnavailable as exc:
        raise ToolError("GwUnavailable", "gateway unavailable") from exc
    except gw.GwBadResponse as exc:
        data = {"status": exc.status_code} if exc.status_code is not None else None
        raise ToolError("GwBadResponse", "gateway returned bad response", data) from exc

    return _stringify_summary(raw_summary)


def _stringify_summary(raw_summary: Any) -> str:
    payload = raw_summary
    if isinstance(raw_summary, Mapping) and raw_summary.get("ok") is True:
        payload = raw_summary.get("user_summary", payload)

    if isinstance(payload, Mapping):
        for key in ("text", "summary", "description"):
            if key in payload:
                value = payload[key]
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return str(value)
        try:
            return json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), default=str
            )
        except TypeError:
            return str(payload)

    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        return payload

    try:
        return json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), default=str
        )
    except TypeError:
        return str(payload)


async def _call_user_last_training(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    user_id = _coerce_user_id(arguments)
    newest = _parse_date(arguments, "newest")
    oldest = _parse_date(arguments, "oldest")

    if newest is None:
        newest = _today()
    if oldest is None:
        oldest = newest - timedelta(days=_DEFAULT_RANGE_DAYS)

    if oldest > newest:
        raise ToolError("InvalidParams", "oldest date cannot be after newest date")

    try:
        trainings = await gw.get_trainings(
            user_id=user_id, oldest=oldest, newest=newest
        )
    except gw.GwUnavailable as exc:
        raise ToolError("GwUnavailable", "gateway unavailable") from exc
    except gw.GwBadResponse as exc:
        data = {"status": exc.status_code} if exc.status_code is not None else None
        raise ToolError("GwBadResponse", "gateway returned bad response", data) from exc

    filtered = [
        training for training in trainings if not _is_future_training(training, newest)
    ]
    return {"items": filtered}


def _is_future_training(training: Mapping[str, Any], newest: date) -> bool:
    training_date = _extract_training_date(training)
    return training_date is not None and training_date > newest


def _extract_training_date(training: Mapping[str, Any]) -> Optional[date]:
    if not isinstance(training, Mapping):
        return None
    candidates = (
        training.get("date"),
        training.get("start_date"),
        training.get("start_at"),
    )
    for value in candidates:
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, date) and not isinstance(value, datetime):
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
                    continue
    return None


__all__ = [
    "ToolError",
    "call_tool",
    "get_tool_definitions",
    "has_tool",
]
