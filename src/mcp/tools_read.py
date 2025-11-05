# src/mcp/tools_read.py
# STAS MCP — read tools (returns proper JSON), retries, robust ISO date parsing.
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


class ToolError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _ok_json(obj: Any) -> Dict[str, Any]:
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


def _to_date(value: Any) -> Optional[dt.date]:
    """
    Принимает:
      'YYYY-MM-DD'
      'YYYY-MM-DDTHH:MM:SS' / '...Z' / '...+00:00'
    Возвращает date или None.
    """
    if not value:
        return None
    s = str(value).strip()
    # самый дешёвый вариант — взять первые 10 символов, если там YYYY-MM-DD
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return dt.date.fromisoformat(s[:10])
        except Exception:
            pass
    # пробуем полноценный ISO 8601
    try:
        s2 = s.replace("Z", "+00:00")
        return dt.datetime.fromisoformat(s2).date()
    except Exception:
        return None


def _item_date(item: dict) -> Optional[dt.date]:
    """
    Достаём дату из возможных ключей тренировок Intervals:
    'date' | 'start_date_local' | 'start_date' | 'start_time'
    """
    for k in ("date", "start_date_local", "start_date", "start_time"):
        d = _to_date((item or {}).get(k))
        if d:
            return d
    return None


async def _gw_get_json(path: str, token: str, params: Dict[str, Any] | None = None, timeout: float = 25.0) -> Any:
    async with httpx.AsyncClient(base_url=BRIDGE_BASE, timeout=timeout) as cli:
        r = await cli.get(
            path,
            headers={"authorization": f"Bearer {token}", "accept": "application/json"},
            params=params or {},
        )
        r.raise_for_status()
        return r.json()


async def _read_user_summary(user_id: int) -> Any:
    token = _mk_token(user_id)

    async def once():
        return await _gw_get_json("/api/db/user_summary", token)

    # спецификация: один скрытый повтор; используем второй ответ, если он успешен
    first = await _retry(once, attempts=1)
    try:
        second = await _retry(once, attempts=1)
        return second
    except Exception:
        return first


async def _read_trainings(
    user_id: int, oldest: Optional[str], newest: Optional[str]
) -> Tuple[List[dict], Dict[str, str]]:
    token = _mk_token(user_id)
    if not newest:
        newest = _today_utc_date().isoformat()
    if not oldest:
        d_new = dt.date.fromisoformat(newest)
        oldest = (d_new - dt.timedelta(days=13)).isoformat()

    params = {"oldest": oldest, "newest": newest}
    raw = await _retry(lambda: _gw_get_json("/trainings", token, params=params))
    items = raw if isinstance(raw, list) else []
    return items, {"oldest": oldest, "newest": newest}


async def _read_plan_events(
    user_id: int, oldest: Optional[str], newest: Optional[str]
) -> Tuple[List[dict], Dict[str, str]]:
    token = _mk_token(user_id)

    # по умолчанию: текущая неделя (пн..вс) в UTC
    if not newest:
        today = _today_utc_date()
        week_start = today - dt.timedelta(days=today.weekday())
        week_end = week_start + dt.timedelta(days=6)
        oldest = oldest or week_start.isoformat()
        newest = week_end.isoformat()
    elif not oldest:
        d_new = dt.date.fromisoformat(newest)
        oldest = (d_new - dt.timedelta(days=6)).isoformat()

    # План = события календаря WORKOUT (или без category с фильтрацией — но здесь используем WORKOUT)
    params = {"oldest": oldest, "newest": newest, "category": "WORKOUT"}
    raw = await _retry(lambda: _gw_get_json("/icu/events", token, params=params))
    events = raw if isinstance(raw, list) else []

    # Оставляем только плановые: category∈{WORKOUT,PLAN} или external_id startswith 'plan:'
    filtered: List[dict] = []
    for e in events:
        cat = (e or {}).get("category")
        ext = (e or {}).get("external_id") or ""
        if cat in ("WORKOUT", "PLAN") or (isinstance(ext, str) and ext.startswith("plan:")):
            filtered.append(e)

    filtered.sort(key=lambda ev: ((_item_date(ev) or dt.date.min), (ev or {}).get("start_date_local") or ""))
    return filtered, {"oldest": oldest or "", "newest": newest or ""}


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


async def call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    import json as _json  # локальный импорт, чтобы не конфликтовать с глобальным
    try:
        # ← НОВОЕ: приводим строковые args к объекту
        if isinstance(arguments, str):
            try:
                arguments = _json.loads(arguments)
            except Exception as _e:
                raise ToolError(424, f"invalid arguments (expected JSON object, got str): {type(arguments)}: {_e}")

        if not isinstance(arguments, dict):
            raise ToolError(424, f"invalid arguments type: {type(arguments)} (expected object)")

        if name == "user.summary.fetch":
            user_id = int(arguments.get("user_id"))
            summary = await _read_user_summary(user_id)
            return _ok_json(summary)

        if name == "user.last_training.fetch":
            user_id = int(arguments.get("user_id"))
            oldest = arguments.get("oldest")
            newest = arguments.get("newest")
            items, rng = await _read_trainings(user_id, oldest, newest)

            # Парсим даты у каждого элемента; фильтруем будущее; берём максимум по дате
            newest_d = _to_date(rng.get("newest"))
            with_dates: List[Tuple[dt.date, dict]] = []
            for it in items:
                d = _item_date(it)
                if not d:
                    continue
                if newest_d and d > newest_d:
                    continue
                with_dates.append((d, it))

            last = with_dates and sorted(with_dates, key=lambda x: x[0])[-1][1] or None
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
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(424, f"tool '{name}' failed: {exc}")

    raise ToolError(404, f"unknown tool '{name}'")

