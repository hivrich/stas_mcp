# src/mcp/tools_read.py
# STAS MCP — read tools (returns proper JSON content), retries, no TaskGroup.
# Tools: user.summary.fetch, user.last_training.fetch, plan.list

from __future__ import annotations
import asyncio
import base64
import datetime as dt
import json
from typing import Any, Dict, List, Optional, Tuple

import httpx

MANIFEST_SCHEMA_URI = "http://json-schema.org/draft-07/schema#"
BRIDGE_BASE = "https://intervals.stas.run/gw"


# ---------- error type forwarded to server ----------
class ToolError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# ---------- helpers ----------
def _ok_json(obj: Any) -> Dict[str, Any]:
    # server expects already-wrapped MCP content
    return {"content": [{"type": "json", "json": obj}]}

def _mk_token(user_id: int) -> str:
    payload = {"uid": int(user_id)}
    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"t_{b64}"

async def _retry(fn, attempts: int = 2, delay: float = 0.3):
    last_exc = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if i + 1 < attempts:
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]

def _today_utc_date() -> dt.date:
    return dt.datetime.utcnow().date()


# ---------- low-level GW calls ----------
async def _gw_get_json(path: str, token: str, params: Dict[str, Any] | None = None, timeout: float = 25.0) -> Any:
    async with httpx.AsyncClient(base_url=BRIDGE_BASE, timeout=timeout) as cli:
        r = await cli.get(path, headers={"authorization": f"Bearer {token}", "accept": "application/json"}, params=params or {})
        r.raise_for_status()
        return r.json()


# ---------- high-level reads ----------
async def _read_user_summary(user_id: int) -> Any:
    token = _mk_token(user_id)

    async def once():
        return await _gw_get_json("/api/db/user_summary", token)

    # Спецификация: делаем один скрытый повтор и используем второй ответ.
    first = await _retry(once, attempts=1)
    try:
        second = await _retry(once, attempts=1)
        return second
    except Exception:
        return first

async def _read_trainings(user_id: int, oldest: Optional[str], newest: Optional[str]) -> Tuple[List[dict], Dict[str, str]]:
    token = _mk_token(user_id)
    if not newest:
        newest = _today_utc_date().isoformat()
    if not oldest:
        d_new = dt.date.fromisoformat(newest)
        oldest = (d_new - dt.timedelta(days=13)).isoformat()

    params = {"oldest": oldest, "newest": newest}
    data = await _retry(lambda: _gw_get_json("/trainings", token, params=params))
    items = data if isinstance(data, list) else []
    return items, {"oldest": oldest, "newest": newest}

async def _read_plan_events(user_id: int, oldest: Optional[str], newest: Optional[str]) -> Tuple[List[dict], Dict[str, str]]:
    token = _mk_token(user_id)

    # По умолчанию читаем текущую неделю (пн..вс) по UTC
    if not newest:
        today = _today_utc_date()
        week_start = today - dt.timedelta(days=today.weekday())
        week_end = week_start + dt.timedelta(days=6)
        oldest = oldest or week_start.isoformat()
        newest = week_end.isoformat()
    elif not oldest:
        d_new = dt.date.fromisoformat(newest)
        oldest = (d_new - dt.timedelta(days=6)).isoformat()

    # Правило: плановые тренировки — calendar events категории WORKOUT
    params = {"oldest": oldest, "newest": newest, "category": "WORKOUT"}
    raw = await _retry(lambda: _gw_get_json("/icu/events", token, params=params))
    events = raw if isinstance(raw, list) else []

    # Фильтрация: только плановые
    filtered: List[dict] = []
    for e in events:
        cat = (e or {}).get("category")
        ext = (e or {}).get("external_id") or ""
        if cat in ("WORKOUT", "PLAN") or (isinstance(ext, str) and ext.startswith("plan:")):
            filtered.append(e)

    # Отсортировать и вернуть
    def _sort_key(ev: dict) -> Tuple[str, str]:
        return ((ev or {}).get("date") or "", (ev or {}).get("start_date_local") or "")
    filtered.sort(key=_sort_key)
    return filtered, {"oldest": oldest or "", "newest": newest or ""}


# ---------- tool definitions ----------
_TOOLS: Dict[str, Dict[str, Any]] = {
    "user.summary.fetch": {
        "name": "user.summary.fetch",
        "description": "Read user summary from STAS GW; returns JSON content",
        "inputSchema": {
            "$schema": MANIFEST_SCHEMA_URI,
            "type": "object",
            "required": ["user_id"],
            "properties": {
                "user_id": {"type": "integer"},
                "connection_id": {"type": "string"},
            },
        },
    },
    "user.last_training.fetch": {
        "name": "user.last_training.fetch",
        "description": "Read trainings in a window (default last 14 days) and return last finished",
        "inputSchema": {
            "$schema": MANIFEST_SCHEMA_URI,
            "type": "object",
            "required": ["user_id"],
            "properties": {
                "user_id": {"type": "integer"},
                "oldest": {"type": "string"},
                "newest": {"type": "string"},
                "connection_id": {"type": "string"},
            },
        },
    },
    "plan.list": {
        "name": "plan.list",
        "description": "List plan events (WORKOUT|PLAN) in a range; defaults to current week",
        "inputSchema": {
            "$schema": MANIFEST_SCHEMA_URI,
            "type": "object",
            "required": ["athlete_id"],
            "properties": {
                "athlete_id": {"type": ["integer", "string"]},
                "oldest": {"type": "string"},
                "newest": {"type": "string"},
                "limit": {"type": "integer"},
                "connection_id": {"type": "string"},
            },
        },
    },
}

def get_tool_definitions() -> List[Dict[str, Any]]:
    return [_TOOLS[k] for k in ("user.summary.fetch", "user.last_training.fetch", "plan.list")]

def has_tool(name: str) -> bool:
    return name in _TOOLS


# ---------- dispatcher ----------
async def call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if name == "user.summary.fetch":
            user_id = int(arguments.get("user_id"))
            summary = await _read_user_summary(user_id)
            return _ok_json(summary)

        if name == "user.last_training.fetch":
            user_id = int(arguments.get("user_id"))
            oldest = arguments.get("oldest")
            newest = arguments.get("newest")
            items, rng = await _read_trainings(user_id, oldest, newest)

            last = None
            if items:
                # отфильтруем будущее и возьмём максимум по дате
                newest_d = None
                try:
                    newest_d = dt.date.fromisoformat(rng["newest"])
                except Exception:
                    pass
                items_sorted = sorted(items, key=lambda x: (x or {}).get("date") or "")
                if newest_d:
                    items_sorted = [x for x in items_sorted if (x.get("date") and dt.date.fromisoformat(x["date"]) <= newest_d)]
                last = items_sorted[-1] if items_sorted else None

            return _ok_json({"ok": True, "last": last, "count": len(items), "range": rng})

        if name == "plan.list":
            aid = arguments.get("athlete_id", arguments.get("user_id"))
            user_id = int(aid)
            oldest = arguments.get("oldest")
            newest = arguments.get("newest")
            limit = arguments.get("limit")

            events, rng = await _read_plan_events(user_id, oldest, newest)
            if isinstance(limit, int) and limit > 0:
                events = events[-limit:]

            return _ok_json({"ok": True, "items": events, "count": len(events), "range": rng})

    except httpx.HTTPStatusError as exc:
        raise ToolError(424, f"upstream {exc.response.status_code}: {exc}")
    except Exception as exc:
        raise ToolError(424, f"tool '{name}' failed: {exc}")

    raise ToolError(404, f"unknown tool '{name}'")
