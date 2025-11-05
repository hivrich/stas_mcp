from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
import pathlib
import re
import time
from typing import Any, Dict, List, Optional, Sequence

import httpx
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jsonschema import Draft7Validator
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.cors import CORSMiddleware

from .linking import get_status as linking_get_status
from .linking import set_linked as linking_set_linked
from .linking import set_pending as linking_set_pending
from .mcp import resources_user as mcp_resources_user
from .mcp import tools_plan_write_ext, tools_read as mcp_tools_read
from .mcp import tools_session as mcp_tools_session
from .routes.read_user import router as read_user_router
from .utils.plan_external_id import normalize_plan_external_id

try:
    from .config import settings
except ImportError:  # pragma: no cover - script mode fallback
    from config import settings  # type: ignore


BASE = pathlib.Path(__file__).resolve()
ROOT_DIR = BASE.parent.parent
APP_DIR = BASE.parent
ASSETS_DIR = APP_DIR / "assets"
SCHEMA_PATH = ASSETS_DIR / "schema.plan.json"

DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LINKS_FILE = DATA_DIR / "links.json"
AUDIT_FILE = DATA_DIR / "audit.log"
if not LINKS_FILE.exists():
    LINKS_FILE.write_text("{}", encoding="utf-8")
AUDIT_FILE.touch(exist_ok=True)


logger = logging.getLogger(__name__)


def load_schema() -> Dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


PLAN_SCHEMA = load_schema()
PLAN_VALIDATOR = Draft7Validator(PLAN_SCHEMA)

MCP_PROTOCOL_VERSION = "2025-06-18"

BRIDGE_BASE = settings.BRIDGE_BASE.rstrip("/")
MODE = "bridge" if BRIDGE_BASE else "stub"

app = FastAPI()

# === httpx client pool (ускоряет апстрим-вызовы) ===
HTTP_CLIENT: httpx.AsyncClient | None = None

@app.on_event("startup")
async def _startup() -> None:
    global HTTP_CLIENT
    HTTP_CLIENT = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        transport=httpx.AsyncHTTPTransport(retries=2),
        headers={"User-Agent": "ChatGPT-User/1.0"},
    )

@app.on_event("shutdown")
async def _shutdown() -> None:
    global HTTP_CLIENT
    if HTTP_CLIENT:
        await HTTP_CLIENT.aclose()
        HTTP_CLIENT = None
# === /httpx client pool ===

# CORS под ChatGPT
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://chatgpt.com",
        "https://staging.chatgpt.com",
        "https://chat.openai.com",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "authorization", "accept"],
    max_age=86400,
)

app.include_router(read_user_router)

MANIFEST_SCHEMA_URI = "http://json-schema.org/draft-07/schema#"


def _draft_input_schema() -> Dict[str, Any]:
    schema = json.loads(json.dumps(PLAN_SCHEMA))
    external_id_schema = schema.get("properties", {}).get("external_id")
    if isinstance(external_id_schema, dict) and "description" not in external_id_schema:
        external_id_schema["description"] = "External ID of the plan"
    return schema


def _plan_tool_definitions(draft_schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    base_tools = [
        {
            "id": "plan.validate",
            "name": "plan.validate",
            "description": "Validate training plan draft against schema.plan.json",
            "inputSchema": {
                "$schema": MANIFEST_SCHEMA_URI,
                "type": "object",
                "required": ["draft"],
                "properties": {
                    "draft": draft_schema,
                    "connection_id": {"type": "string"},
                },
            },
        },
        {
            "id": "plan.publish",
            "name": "plan.publish",
            "description": "Publish a plan; requires confirm:true; idempotent by external_id",
            "inputSchema": {
                "$schema": MANIFEST_SCHEMA_URI,
                "type": "object",
                "required": ["external_id", "draft", "confirm"],
                "properties": {
                    "external_id": {"type": "string"},
                    "draft": draft_schema,
                    "confirm": {"type": "boolean"},
                    "connection_id": {"type": "string"},
                },
            },
        },
        {
            "id": "plan.delete",
            "name": "plan.delete",
            "description": "Delete a plan by external_id; requires confirm:true",
            "inputSchema": {
                "$schema": MANIFEST_SCHEMA_URI,
                "type": "object",
                "required": ["external_id", "confirm"],
                "properties": {
                    "external_id": {"type": "string"},
                    "confirm": {"type": "boolean"},
                    "connection_id": {"type": "string"},
                },
            },
        },
    ]

    extra_tools = list(tools_plan_write_ext.get_tool_definitions(draft_schema))
    return [*base_tools, *extra_tools]


def _combined_tool_definitions() -> List[Dict[str, Any]]:
    # Для UI/манифеста
    draft_schema = _draft_input_schema()
    plan_tools = _plan_tool_definitions(draft_schema)
    read_tools = mcp_tools_read.get_tool_definitions()
    session_tools = mcp_tools_session.get_tool_definitions()
    return [*plan_tools, *read_tools, *session_tools]


def build_manifest() -> Dict[str, Any]:
    mode = "bridge" if BRIDGE_BASE else "stub"
    base_tools = _combined_tool_definitions()
    tools = [
        {
            "id": tool["id"],
            "name": tool["name"],
            "description": tool["description"],
            "method": "POST",
            "path": f"/mcp/tool/{tool['name']}",
            "input_schema": tool["inputSchema"],
            "inputSchema": tool["inputSchema"],
        }
        for tool in base_tools
    ]
    resource_entries = [
        {
            "name": resource["uri"],
            "path": f"/mcp/resource/{resource['uri']}",
            "method": "GET",
            "description": resource.get("description", ""),
        }
        for resource in mcp_resources_user.list_resources()
    ]
    resource_entries.append(
        {
            "name": "schema.plan.json",
            "path": "/mcp/resource/schema.plan.json",
            "method": "GET",
        }
    )

    manifest = {
        "server": {"name": "stas-mcp-bridge", "version": "1"},
        "mode": mode,
        "resources": resource_entries,
        "tools": tools,
        "actions": [
            {
                k: v
                for k, v in tool.items()
                if k in ("id", "name", "description", "method", "path", "input_schema", "inputSchema")
            }
            for tool in tools
        ],
        "sse": {"path": "/sse"},
    }
    return manifest


def _as_actions_from_tools(tools: list[dict]) -> list[dict]:
    actions: list[dict] = []
    for t in tools or []:
        _id = t.get("id") or t.get("name")
        _nm = t.get("name") or _id
        _ds = t.get("description", "")
        sch = t.get("input_schema") or t.get("inputSchema") or {"type": "object"}
        actions.append({
            "id": _id,
            "name": _nm,
            "description": _ds,
            "input_schema": sch,
            "inputSchema": sch,
        })
    return actions


def _normalize_manifest_for_ui(manifest: dict) -> dict:
    m = dict(manifest or {})
    tools = m.get("tools") or m.get("actions") or []
    m["actions"] = _as_actions_from_tools(tools)
    norm_tools: list[dict] = []
    for t in tools:
        t = dict(t)
        sch = t.get("input_schema") or t.get("inputSchema")
        if sch is not None:
            t["input_schema"] = sch
            t["inputSchema"] = sch
        if "name" not in t and "id" in t:
            t["name"] = t["id"]
        norm_tools.append(t)
    m["tools"] = norm_tools
    return m


def build_tools_for_rpc() -> List[Dict[str, Any]]:
    # Для tools/list показываем плановые + читающие + сессионные (если нужны)
    plan_tools = _plan_tool_definitions(_draft_input_schema())
    read_tools = mcp_tools_read.get_tool_definitions()
    session_tools = mcp_tools_session.get_tool_definitions()

    tools: List[Dict[str, Any]] = [
        {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["inputSchema"],
        }
        for tool in plan_tools
    ]
    tools.extend(read_tools)
    tools.extend(session_tools)
    return tools


def _mcp_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "Access-Control-Allow-Origin": "*",
        "Vary": "Origin",
    }
    if extra:
        headers.update(extra)
    return headers


def rpc_ok(rpc_id: Any, result: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result}, status_code=status_code, headers=_mcp_headers())


def rpc_err(
    rpc_id: Any,
    code: int | str,
    message: str,
    data: Any = None,
    *,
    status_code: int = 200,
) -> JSONResponse:
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        payload["error"]["data"] = data
    return JSONResponse(payload, status_code=status_code, headers=_mcp_headers())


def _tool_json_content(result: Any) -> Dict[str, Any]:
    return {"content": [{"type": "json", "json": result}]}


@app.options("/mcp")
async def mcp_options() -> Response:
    return Response(
        status_code=200,
        headers=_mcp_headers({
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }),
    )


def _manifest_response() -> JSONResponse:
    manifest = build_manifest()
    manifest = _normalize_manifest_for_ui(manifest)
    return JSONResponse(manifest, headers=_mcp_headers({"Access-Control-Allow-Methods": "GET, POST, OPTIONS"}))


@app.get("/mcp")
async def mcp_manifest() -> JSONResponse:
    return _manifest_response()


@app.post("/mcp")
async def mcp_rpc(request: Request) -> JSONResponse:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower() if content_type else ""
    if media_type != "application/json":
        return rpc_err(None, -32600, "Unsupported Media Type: expected application/json", status_code=415)

    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover - defensive parsing
        return rpc_err(None, -32700, "Parse error", str(exc))

    if not isinstance(payload, dict):
        return rpc_err(None, -32600, "Invalid request: expected object")

    rpc_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if not isinstance(method, str):
        return rpc_err(rpc_id, -32600, "Invalid request: method must be string")

    if params and not isinstance(params, dict):
        return rpc_err(rpc_id, -32602, "Invalid params: expected object")

    # --- initialize ---
    if method == "initialize":
        proto = params.get("protocolVersion") or MCP_PROTOCOL_VERSION
        result = {
            "protocolVersion": proto,
            "capabilities": {
                "tools": {"list": True, "call": True, "listChanged": True},
                "resources": {"list": True, "read": True},
            },
            "serverInfo": {"name": "stas-mcp-bridge", "version": "1.0.0"},
        }
        return rpc_ok(rpc_id, result)

    # --- tools/list ---
    if method == "tools/list":
        tools = build_tools_for_rpc()
        return rpc_ok(rpc_id, {"tools": tools})

    # --- tools/call ---
    if method == "tools/call":
        name = str(params.get("name") or "").strip()
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return rpc_err(rpc_id, -32602, "Invalid params: arguments must be object")

        connection_id = (
            request.headers.get("x-connection-id")
            or request.headers.get("x-conn")
            or request.query_params.get("cid")
            or arguments.get("connection_id")
        )

        try:
            if mcp_tools_session.has_tool(name):
                result = await mcp_tools_session.call_tool(name, arguments)
                return rpc_ok(rpc_id, result)

            if mcp_tools_read.has_tool(name):
                result = await mcp_tools_read.call_tool(name, arguments)
                return rpc_ok(rpc_id, result)

            if tools_plan_write_ext.has_tool(name):
                payload_in = dict(arguments)
                if connection_id and not payload_in.get("connection_id"):
                    payload_in["connection_id"] = connection_id
                user_id = _resolve_user_id(connection_id)
                if not user_id:
                    hint = _link_hint(request, connection_id)
                    return rpc_ok(rpc_id, _tool_json_content(hint))
                try:
                    result = await tools_plan_write_ext.call_tool(name, payload_in, user_id=user_id)
                except tools_plan_write_ext.ToolError as exc:
                    return rpc_err(rpc_id, exc.code, exc.message, exc.data)
                return rpc_ok(rpc_id, _tool_json_content(result))

            if name == "plan.validate":
                payload_in = dict(arguments)
                if connection_id and not payload_in.get("connection_id"):
                    payload_in["connection_id"] = connection_id
                result = await plan_validate(payload=payload_in)
                return rpc_ok(rpc_id, _tool_json_content(result))

            if name == "plan.publish":
                payload_in = dict(arguments)
                if connection_id and not payload_in.get("connection_id"):
                    payload_in["connection_id"] = connection_id
                result = await plan_publish(request, payload_in)
                return rpc_ok(rpc_id, _tool_json_content(result))

            if name == "plan.delete":
                payload_in = dict(arguments)
                if connection_id and not payload_in.get("connection_id"):
                    payload_in["connection_id"] = connection_id
                result = await plan_delete(request, payload_in)
                return rpc_ok(rpc_id, _tool_json_content(result))

            return rpc_err(rpc_id, -32601, f"Method tools/call: unknown tool '{name}'")
        except (mcp_tools_read.ToolError, tools_plan_write_ext.ToolError) as exc:  # type: ignore[attr-defined]
            return rpc_err(rpc_id, exc.code, exc.message, getattr(exc, "data", None))
        except Exception as exc:  # pragma: no cover - defensive guard
            return rpc_err(rpc_id, -32000, "Tool execution error", str(exc))

    # --- resources/* ---
    if method == "resources/list":
        resources = mcp_resources_user.list_resources()
        return rpc_ok(rpc_id, {"resources": resources})

    if method == "resources/read":
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri.strip():
            return rpc_err(rpc_id, -32602, "Invalid params: uri must be a non-empty string")
        try:
            result = await mcp_resources_user.read_resource(uri.strip())
        except mcp_resources_user.ResourceError as exc:  # type: ignore[attr-defined]
            return rpc_err(rpc_id, exc.code, exc.message, getattr(exc, "data", None))
        except Exception as exc:  # pragma: no cover - defensive guard
            return rpc_err(rpc_id, -32001, "Resource read error", str(exc))
        return rpc_ok(rpc_id, result)

    return rpc_err(rpc_id, -32601, f"Unknown method '{method}'")


# ===== helpers =====

def _load_links() -> Dict[str, str]:
    try:
        return json.loads(LINKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_links(data: Dict[str, str]) -> None:
    LINKS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_audit(entry: Dict[str, Any]) -> None:
    record = json.dumps(entry, ensure_ascii=False)
    with AUDIT_FILE.open("a", encoding="utf-8") as fh:
        fh.write(record + "\n")


def _resolve_connection_id(req: Request, payload: Dict[str, Any]) -> Optional[str]:
    header_value = req.headers.get("x-connection-id") or req.headers.get("x-conn")
    if header_value:
        return header_value
    query_value = req.query_params.get("cid") or req.query_params.get("connection_id")
    if query_value:
        return query_value
    if isinstance(payload, dict):
        value = payload.get("connection_id")
        if isinstance(value, str) and value:
            return value
    return None


def _resolve_user_id(conn_id: Optional[str]) -> Optional[str]:
    if not conn_id:
        return None
    return _load_links().get(conn_id)


def _bearer(uid: str) -> str:
    payload = json.dumps({"uid": int(uid)})
    token = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"t_{token}"


async def gw(
    method: str,
    path: str,
    *,
    uid: str,
    params: Optional[Dict[str, Any]] = None,
    json_payload: Optional[Dict[str, Any]] = None,
    ua: str = "ChatGPT-User/1.0",
) -> Any:
    if not BRIDGE_BASE:
        raise httpx.RequestError("BRIDGE_BASE not configured")
    url = f"{BRIDGE_BASE}{path}"
    headers = {"Authorization": f"Bearer {_bearer(uid)}", "User-Agent": ua}

    assert HTTP_CLIENT is not None, "HTTP client not initialized"
    t0 = time.perf_counter()
    try:
        resp = await HTTP_CLIENT.request(method.upper(), url, params=params, json=json_payload, headers=headers)
        resp.raise_for_status()
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text
    except Exception as exc:  # pragma: no cover - trace
        dt_ms = int((time.perf_counter() - t0) * 1000)
        logger.error("gw %s %s failed in %dms: %s", method, path, dt_ms, exc)
        raise


def _draft_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    draft = payload.get("draft")
    if isinstance(draft, dict):
        return draft
    return {k: v for k, v in payload.items() if k != "confirm"}


def _request_ua(request: Request) -> str:
    return request.headers.get("user-agent") or "ChatGPT-User/1.0"


def _unique_days(events: Sequence[Dict[str, Any]]) -> int:
    days = {str(event.get("start_date_local", ""))[:10] for event in events if isinstance(event, dict)}
    return len({d for d in days if d})


def _event_name(*titles: Optional[str]) -> str:
    for title in titles:
        if isinstance(title, str) and title.strip():
            return title.strip()[:40]
    return "Workout"


def _build_events(draft: Dict[str, Any], external_id: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    days = draft.get("days")
    if not isinstance(days, list):
        return events
    for day in days:
        if not isinstance(day, dict):
            continue
        date = day.get("date")
        if not isinstance(date, str) or not date:
            continue
        day_title = day.get("title") if isinstance(day.get("title"), str) else None
        blocks = [block for block in (day.get("blocks") or []) if isinstance(block, dict)] if isinstance(day.get("blocks"), list) else []
        target_blocks = blocks if blocks else [None]
        for block in target_blocks:
            block_title = block.get("title") if isinstance(block, dict) and isinstance(block.get("title"), str) else None
            events.append({
                "start_date_local": f"{date}T09:00:00",
                "type": "Workout",
                "name": _event_name(block_title, day_title),
                "description": day_title or block_title or "Workout",
                "category": "WORKOUT",
                "external_id": external_id,
            })
    return events


def _parse_iso(value: str) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _pick_last_training(payload: Any) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        candidates = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        for key in ("items", "data", "trainings", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = [item for item in value if isinstance(item, dict)]
                break
        else:
            if all(isinstance(v, dict) for v in payload.values()):
                candidates = [dict(payload)]
    if not candidates:
        return None

    finished: List[Dict[str, Any]] = []
    for item in candidates:
        status = str(item.get("status") or item.get("state") or "").upper()
        if any(flag in status for flag in ("FINISH", "COMPLETE", "DONE")):
            finished.append(item)
    pool = finished or candidates

    def key(item: Dict[str, Any]) -> dt.datetime:
        for field in ("finished_at", "completed_at", "end_at", "end_time", "start_time", "start_date_local", "date"):
            value = item.get(field)
            if isinstance(value, str):
                parsed = _parse_iso(value)
                if parsed:
                    return parsed
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)

    return max(pool, key=key)


def _window_for_external(external_id: str) -> Dict[str, str]:
    today = dt.date.today()
    match = re.fullmatch(r"plan:(\d{4})-w(\d{2})", external_id or "")
    if match:
        year = int(match.group(1))
        week = int(match.group(2))
        monday = dt.date.fromisocalendar(year, week, 1)
        sunday = dt.date.fromisocalendar(year, week, 7)
        return {"oldest": monday.isoformat(), "newest": sunday.isoformat()}
    return {"oldest": today.isoformat(), "newest": today.isoformat()}


def _link_hint(request: Request, connection_id: Optional[str]) -> Dict[str, Any]:
    try:
        base = str(request.url_for("link_page"))
    except Exception:
        base = "/link"
    if connection_id:
        uri = f"{base}?connection_id={connection_id}"
    else:
        uri = base
    return {"ok": False, "need_link": True, "hint": "Open /link and map connection_id→user_id", "uri": uri}


# ===== simple HTTP endpoints =====

@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "ts": int(time.time()), "mode": MODE}


@app.get("/whoami")
async def whoami() -> Dict[str, Any]:
    links = _load_links()
    return {"ok": True, "mode": MODE, "bridge_base": BRIDGE_BASE, "links": len(links)}


def _mask_connection_id(connection_id: str) -> str:
    if len(connection_id) <= 4:
        return "***"
    return f"{connection_id[:4]}***"


def _linking_status(connection_id: str) -> Dict[str, Any]:
    persisted = _resolve_user_id(connection_id)
    if persisted:
        try:
            linking_set_linked(connection_id, int(persisted))
        except (TypeError, ValueError):
            logger.debug("Skipping persisted user_id that is not int for %s", _mask_connection_id(connection_id))
    status = linking_get_status(connection_id)
    if status.get("linked") and "user_id" not in status and persisted:
        try:
            status["user_id"] = int(persisted)
        except (TypeError, ValueError):
            pass
    return status


@app.get("/_whoami")
async def api_whoami(request: Request) -> Dict[str, Any]:
    connection_id = _resolve_connection_id(request, {})
    if not connection_id:
        raise HTTPException(status_code=422, detail="connection_id is required")
    status = _linking_status(connection_id)
    response: Dict[str, Any] = {"connection_id": connection_id, "linked": bool(status.get("linked"))}
    if status.get("user_id") is not None:
        response["user_id"] = status["user_id"]
    return response


@app.get("/_link")
async def api_link(connection_id: str = Query(..., min_length=1)) -> Dict[str, Any]:
    linking_set_pending(connection_id)
    status = _linking_status(connection_id)
    logger.info("linking.request connection=%s", _mask_connection_id(connection_id))
    instructions = [
        "Откройте страницу авторизации STAS и подтвердите доступ для соединения.",
        "Скопируйте одноразовый code и вызовите POST /auth/exchange",
        'Передайте JSON: {"connection_id": "%s", "code": "<CODE>"} внутри MCP.' % connection_id,
    ]
    payload: Dict[str, Any] = {"connection_id": connection_id, "linked": bool(status.get("linked")), "instructions": instructions}
    if status.get("user_id") is not None:
        payload["user_id"] = status["user_id"]
    return payload


@app.get("/link", name="link_page")
@app.get("/_/link")
async def link_page(request: Request) -> HTMLResponse:
    connection_id = request.query_params.get("connection_id", "")
    template = """
    <meta charset='utf-8'><style>body{font:14px system-ui;margin:24px;max-width:560px}label{display:block;margin:12px 0}</style>
    <h2>Link MCP connection</h2>
    <form method="post" action="/link">
      <label>connection_id <input name="connection_id" value="{connection}" required></label>
      <label>user_id <input name="user_id" placeholder="12345" required></label>
      <button type="submit">Save link</button>
    </form>
    <p>Enter the STAS user ID once per connection. Future calls reuse the stored mapping.</p>
    """
    html = template.format(connection=connection_id)
    return HTMLResponse(html)


@app.post("/link")
@app.post("/_/link")
async def link_save(request: Request) -> Dict[str, Any]:
    form = await request.form()
    conn_id = str(form.get("connection_id") or "").strip()
    user_id = str(form.get("user_id") or "").strip()
    if not (conn_id and user_id):
        return {"ok": False, "error": "bad_input"}
    try:
        int(user_id)
    except ValueError:
        return {"ok": False, "error": "invalid_user_id"}
    links = _load_links()
    links[conn_id] = user_id
    _save_links(links)
    return {"ok": True, "linked": {"connection_id": conn_id, "user_id": user_id}}


@app.post("/mcp/connect")
async def mcp_connect(request: Request, payload: Optional[Dict[str, Any]] = Body(default=None)) -> Dict[str, Any]:
    payload = payload or {}
    conn_id = _resolve_connection_id(request, payload)
    if not conn_id:
        return {"type": "error", "error": "missing_connection_id"}
    user_id = _resolve_user_id(conn_id)
    if not user_id:
        link_url = str(request.url_for("link_page")) + f"?connection_id={conn_id}"
        return {"type": "navigate", "uri": link_url}
    manifest = build_manifest()
    return {"type": "connected", "manifest": manifest, "connection_id": conn_id}


@app.get("/mcp/manifest")
async def http_manifest() -> JSONResponse:
    return _manifest_response()


@app.get("/mcp/resource/{name}")
async def resource_get(name: str, request: Request) -> Any:
    conn_id = _resolve_connection_id(request, {})
    user_id = _resolve_user_id(conn_id)
    if not user_id:
        return JSONResponse(_link_hint(request, conn_id), status_code=403)
    ua = _request_ua(request)

    if name == "current.json":
        try:
            return await gw("GET", "/api/db/user_summary", uid=user_id, ua=ua)
        except httpx.HTTPError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)

    if name == "last_training.json":
        today = dt.date.today()
        oldest = (today - dt.timedelta(days=14)).isoformat()
        newest = today.isoformat()
        try:
            payload = await gw(
                "GET",
                "/trainings",
                uid=user_id,
                params={"oldest": oldest, "newest": newest},
                ua=ua,
            )
        except httpx.HTTPError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
        latest = _pick_last_training(payload)
        count = 0
        if isinstance(payload, list):
            count = len(payload)
        elif isinstance(payload, dict):
            for key in ("items", "data", "trainings", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    count = len(value)
                    break
        return {"ok": bool(latest), "last": latest, "range": {"oldest": oldest, "newest": newest}, "count": count}

    if name == "schema.plan.json":
        return JSONResponse(load_schema())

    return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)


# ===== plan tools HTTP handlers (переиспользуются в tools/call) =====

@app.post("/mcp/tool/plan.validate")
async def plan_validate(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    draft = _draft_from_payload(payload)
    if not draft:
        return {"ok": False, "errors": [{"path": [], "message": "Invalid plan payload"}], "warnings": [], "diff": {}}

    errors = [{"path": list(error.absolute_path), "message": error.message} for error in PLAN_VALIDATOR.iter_errors(draft)]
    return {"ok": not errors, "errors": errors, "warnings": [], "diff": {}}


@app.post("/mcp/tool/plan.publish")
async def plan_publish(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid_payload"}

    draft = _draft_from_payload(payload)
    draft_days = draft.get("days") if isinstance(draft.get("days"), list) else None
    raw_external_id_input = str(payload.get("external_id") or draft.get("external_id") or "")
    raw_external_id, normalized_external_id = normalize_plan_external_id(raw_external_id_input, days=draft_days)
    events = _build_events(draft, normalized_external_id)

    conn_id = _resolve_connection_id(request, payload)
    user_id = _resolve_user_id(conn_id)
    if not user_id:
        return _link_hint(request, conn_id)

    if not events:
        return {"ok": False, "error": "no_events", "hint": "Provide at least one day/block"}

    ua = _request_ua(request)
    params = {"external_id_prefix": "plan:"}

    summary: Dict[str, Any] = {}
    if not payload.get("confirm"):
        try:
            dry_response = await gw(
                "POST",
                "/icu/events",
                uid=user_id,
                params={**params, "dry_run": "true"},
                json_payload={"events": events, "external_id": normalized_external_id},
                ua=ua,
            )
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc), "stage": "dry_run"}

        return {
            "ok": False,
            "need_confirm": True,
            "hint": "Add confirm:true",
            "external_id": raw_external_id,
            "external_id_normalized": normalized_external_id,
            "status": "preview",
            "days_written": _unique_days(events),
            "events": len(events),
            "dry_run": {"count": int(dry_response.get("count", len(events)) if isinstance(dry_response, dict) else len(events))},
        }

    try:
        real_response = await gw(
            "POST",
            "/icu/events",
            uid=user_id,
            params={**params, "dry_run": "false"},
            json_payload={"events": events, "external_id": normalized_external_id},
            ua=ua,
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc), "stage": "publish"}

    if isinstance(real_response, dict):
        summary = {
            "count": int(real_response.get("count") or real_response.get("events") or len(events)),
            "updated": real_response.get("updated"),
        }
        if "etag" in real_response:
            summary["etag"] = real_response.get("etag")
    else:
        summary = {"count": len(events)}

    result = {
        "ok": True,
        "external_id": raw_external_id,
        "external_id_normalized": normalized_external_id,
        "status": "published",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "events": len(events),
        "days_written": _unique_days(events),
        "count": summary.get("count", len(events)),
    }
    if "etag" in summary and summary["etag"]:
        result["etag"] = summary["etag"]
    if summary.get("updated") is not None:
        updated = bool(summary["updated"])
        result["updated"] = updated
        if not updated:
            result["no_op"] = True
    _append_audit({"ts": int(time.time()), "connection_id": conn_id, "user_id": user_id, "op": "publish", "external_id": normalized_external_id, "status": "ok"})
    return result


@app.post("/mcp/tool/plan.delete")
async def plan_delete(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid_payload"}

    external_id = str(payload.get("external_id") or "").strip()
    if not external_id:
        return {"ok": False, "error": "missing_external_id"}

    conn_id = _resolve_connection_id(request, payload)
    user_id = _resolve_user_id(conn_id)
    if not user_id:
        return _link_hint(request, conn_id)

    window = _window_for_external(external_id)

    if not payload.get("confirm"):
        return {"ok": False, "need_confirm": True, "hint": "Add confirm:true", "external_id": external_id, "window": window}

    ua = _request_ua(request)
    try:
        delete_response = await gw("DELETE", "/icu/events", uid=user_id, params={"external_id_prefix": "plan:", **window}, ua=ua)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc), "stage": "delete"}

    _append_audit({"ts": int(time.time()), "connection_id": conn_id, "user_id": user_id, "op": "delete", "external_id": external_id, "status": "ok"})
    return {"ok": True, "external_id": external_id, "window": window, "response": delete_response}


# ===== SSE =====

async def _sse_event_generator(request: Request):
    manifest = build_manifest()
    yield {"event": "manifest", "data": json.dumps(manifest, ensure_ascii=False)}
    while True:
        if await request.is_disconnected():
            break
        yield {"event": "ping", "data": json.dumps({"ts": int(time.time())})}
        await asyncio.sleep(15)


def _sse_response(request: Request) -> EventSourceResponse:
    return EventSourceResponse(_sse_event_generator(request), media_type="text/event-stream", headers={"Access-Control-Allow-Origin": "*"})


@app.get("/sse")
async def sse(request: Request) -> EventSourceResponse:
    return _sse_response(request)


# ===== CLI =====

def main() -> None:  # pragma: no cover - CLI helper
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":  # pragma: no cover
    main()
