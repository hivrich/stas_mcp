from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional

from src.clients import gw
from src.utils.plan_external_id import normalize_plan_external_id

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA_URI = "http://json-schema.org/draft-07/schema#"


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


def get_tool_definitions(_draft_schema: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    definitions = (
        ToolDefinition(
            name="plan.update",
            description="Partially update a previously published plan. Dry-run by default.",
            input_schema={
                "$schema": MANIFEST_SCHEMA_URI,
                "type": "object",
                "required": ["external_id", "patch"],
                "properties": {
                    "external_id": {"type": "string"},
                    "patch": {"type": "object"},
                    "confirm": {
                        "type": "boolean",
                        "default": False,
                        "description": "Set to true to persist changes; default is dry-run.",
                    },
                    "if_match": {
                        "type": ["string", "null"],
                        "description": "ETag of the current plan version.",
                    },
                    "connection_id": {"type": "string"},
                },
            },
        ),
        ToolDefinition(
            name="plan.status",
            description="Fetch publication status and etag for a plan external_id.",
            input_schema={
                "$schema": MANIFEST_SCHEMA_URI,
                "type": "object",
                "required": ["external_id"],
                "properties": {
                    "external_id": {"type": "string"},
                    "connection_id": {"type": "string"},
                },
            },
        ),
        ToolDefinition(
            name="plan.list",
            description="List plans ordered by updated_at desc with optional filters.",
            input_schema={
                "$schema": MANIFEST_SCHEMA_URI,
                "type": "object",
                "properties": {
                    "athlete_id": {"type": "string"},
                    "date_from": {
                        "type": "string",
                        "pattern": r"^\\d{4}-\\d{2}-\\d{2}$",
                        "description": "Start date (YYYY-MM-DD)",
                    },
                    "date_to": {
                        "type": "string",
                        "pattern": r"^\\d{4}-\\d{2}-\\d{2}$",
                        "description": "End date (YYYY-MM-DD)",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                    },
                    "cursor": {"type": ["string", "null"]},
                    "connection_id": {"type": "string"},
                },
            },
        ),
    )
    for definition in definitions:
        yield definition.as_dict()


def has_tool(name: str) -> bool:
    return name in {"plan.update", "plan.status", "plan.list"}


async def call_tool(
    name: str,
    arguments: Mapping[str, Any],
    *,
    user_id: str | int,
) -> Dict[str, Any]:
    user = _normalize_user_id(user_id)
    if name == "plan.update":
        return await _call_plan_update(arguments, user)
    if name == "plan.status":
        return await _call_plan_status(arguments, user)
    if name == "plan.list":
        return await _call_plan_list(arguments, user)
    raise ToolError("InvalidParams", f"Unknown tool '{name}'")


def _normalize_user_id(value: str | int) -> int:
    if isinstance(value, bool):
        raise ToolError("InvalidParams", "user_id must be an integer")
    try:
        user = int(value)
    except (TypeError, ValueError):
        raise ToolError("InvalidParams", "user_id must be an integer") from None
    if user < 0:
        raise ToolError("InvalidParams", "user_id must be non-negative")
    return user


def _coerce_external_id(arguments: Mapping[str, Any]) -> str:
    external_id = arguments.get("external_id")
    if not isinstance(external_id, str) or not external_id.strip():
        raise ToolError("InvalidParams", "external_id must be a non-empty string")
    return external_id.strip()


def _coerce_patch(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    patch = arguments.get("patch")
    if not isinstance(patch, Mapping):
        raise ToolError("InvalidParams", "patch must be an object")
    return dict(patch)


def _coerce_confirm(arguments: Mapping[str, Any]) -> bool:
    confirm = arguments.get("confirm", False)
    if isinstance(confirm, bool):
        return confirm
    raise ToolError("InvalidParams", "confirm must be a boolean")


def _coerce_if_match(arguments: Mapping[str, Any]) -> Optional[str]:
    if "if_match" not in arguments:
        return None
    value = arguments["if_match"]
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ToolError("InvalidParams", "if_match must be a string or null")


async def _call_plan_update(
    arguments: Mapping[str, Any], user_id: int
) -> Dict[str, Any]:
    external_id_raw = _coerce_external_id(arguments)
    patch = _coerce_patch(arguments)
    confirm = _coerce_confirm(arguments)
    if_match = _coerce_if_match(arguments)

    patch_days = patch.get("days") if isinstance(patch.get("days"), list) else None
    external_id, external_id_normalized = normalize_plan_external_id(
        external_id_raw, days=patch_days
    )

    logger.info(
        "plan.update request",
        extra={
            "external_id": external_id,
            "external_id_normalized": external_id_normalized,
            "confirm": confirm,
        },
    )
    try:
        response = await gw.plan_update(
            user_id=user_id,
            external_id=external_id_normalized,
            patch=patch,
            dry_run=not confirm,
            if_match=if_match,
        )
    except gw.GwUnavailable as exc:
        logger.warning(
            "plan.update gateway unavailable", extra={"external_id": external_id}
        )
        raise ToolError("GwUnavailable", "gateway unavailable") from exc
    except gw.GwBadResponse as exc:
        if exc.status_code == 409:
            etag_current = None
            if isinstance(exc.payload, Mapping):
                etag_current = (
                    exc.payload.get("etag_current")
                    or exc.payload.get("etag")
                    or exc.payload.get("current_etag")
                )
            logger.info(
                "plan.update conflict",
                extra={"external_id": external_id, "etag_current": etag_current},
            )
            raise ToolError(
                "Conflict",
                "Plan update conflict",
                data={"etag_current": etag_current},
            ) from exc
        logger.warning(
            "plan.update bad response",
            extra={"external_id": external_id, "status": exc.status_code},
        )
        raise ToolError(
            "GwBadResponse",
            "gateway returned bad response",
            data={"status": exc.status_code},
        ) from exc

    if confirm:
        logger.info(
            "plan.update applied",
            extra={
                "external_id": external_id,
                "external_id_normalized": external_id_normalized,
                "updated": bool(response.get("updated")),
            },
        )
        return {
            "external_id": external_id,
            "external_id_normalized": external_id_normalized,
            "updated": bool(response.get("updated", False)),
            "etag": response.get("etag"),
        }

    logger.info(
        "plan.update dry-run",
        extra={
            "external_id": external_id,
            "external_id_normalized": external_id_normalized,
            "would_change": bool(response.get("would_change")),
        },
    )
    return {
        "external_id": external_id,
        "external_id_normalized": external_id_normalized,
        "would_change": bool(response.get("would_change", False)),
        "diff": response.get("diff", {}),
    }


async def _call_plan_status(
    arguments: Mapping[str, Any], user_id: int
) -> Dict[str, Any]:
    external_id = _coerce_external_id(arguments)
    logger.info("plan.status request", extra={"external_id": external_id})
    try:
        response = await gw.plan_status(user_id=user_id, external_id=external_id)
    except gw.GwUnavailable as exc:
        logger.warning(
            "plan.status gateway unavailable", extra={"external_id": external_id}
        )
        raise ToolError("GwUnavailable", "gateway unavailable") from exc
    except gw.GwBadResponse as exc:
        if exc.status_code == 404:
            logger.info("plan.status missing", extra={"external_id": external_id})
            return {"status": "missing"}
        logger.warning(
            "plan.status bad response",
            extra={"external_id": external_id, "status": exc.status_code},
        )
        raise ToolError(
            "GwBadResponse",
            "gateway returned bad response",
            data={"status": exc.status_code},
        ) from exc

    status = str(response.get("status") or "").lower() or "draft"
    result = {"status": status}
    if etag := response.get("etag"):
        result["etag"] = etag
    if updated_at := response.get("updated_at"):
        result["updated_at"] = updated_at
    logger.info(
        "plan.status success", extra={"external_id": external_id, "status": status}
    )
    return result


def _coerce_limit(arguments: Mapping[str, Any]) -> int:
    value = arguments.get("limit", 50)
    if isinstance(value, bool):
        raise ToolError("InvalidParams", "limit must be an integer")
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise ToolError("InvalidParams", "limit must be an integer") from None
    if limit <= 0:
        raise ToolError("InvalidParams", "limit must be positive")
    if limit > 200:
        limit = 200
    return limit


def _coerce_optional_str(arguments: Mapping[str, Any], key: str) -> Optional[str]:
    if key not in arguments:
        return None
    value = arguments[key]
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ToolError("InvalidParams", f"{key} must be a string or null")


async def _call_plan_list(arguments: Mapping[str, Any], user_id: int) -> Dict[str, Any]:
    limit = _coerce_limit(arguments)
    athlete_id = _coerce_optional_str(arguments, "athlete_id")
    date_from = _coerce_optional_str(arguments, "date_from")
    date_to = _coerce_optional_str(arguments, "date_to")
    cursor = _coerce_optional_str(arguments, "cursor")

    logger.info(
        "plan.list request",
        extra={
            "athlete_id": athlete_id,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        },
    )
    try:
        response = await gw.plan_list(
            user_id=user_id,
            athlete_id=athlete_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            cursor=cursor,
        )
    except gw.GwUnavailable as exc:
        logger.warning("plan.list gateway unavailable")
        raise ToolError("GwUnavailable", "gateway unavailable") from exc
    except gw.GwBadResponse as exc:
        logger.warning("plan.list bad response", extra={"status": exc.status_code})
        raise ToolError(
            "GwBadResponse",
            "gateway returned bad response",
            data={"status": exc.status_code},
        ) from exc

    items = response.get("items")
    if not isinstance(items, list):
        raise ToolError("GwBadResponse", "gateway returned invalid list payload")
    logger.info(
        "plan.list success",
        extra={"count": len(items), "next_cursor": response.get("next_cursor")},
    )
    return {
        "items": items,
        "next_cursor": response.get("next_cursor"),
    }


__all__ = [
    "ToolError",
    "call_tool",
    "get_tool_definitions",
    "has_tool",
]
