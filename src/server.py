from __future__ import annotations

import os, json, time, asyncio, pathlib
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse, HTMLResponse
from sse_starlette.sse import EventSourceResponse
from jsonschema import Draft7Validator

BASE = pathlib.Path(__file__).resolve()
APP_DIR = BASE.parent
ASSETS_DIR = APP_DIR / "assets"
SCHEMA_PATH = ASSETS_DIR / "schema.plan.json"

BRIDGE_BASE = os.getenv("BRIDGE_BASE", "").rstrip("/")
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")
USER_ID_ENV = os.getenv("USER_ID", "").strip()

DATA = APP_DIR / "data"
DATA.mkdir(parents=True, exist_ok=True)
LINKS_FILE = DATA / "links.json"
if not LINKS_FILE.exists():
    LINKS_FILE.write_text("{}", encoding="utf-8")


def load_schema() -> Dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


PLAN_SCHEMA = load_schema()
PLAN_VALIDATOR = Draft7Validator(PLAN_SCHEMA)

app = FastAPI()
MODE = "bridge" if BRIDGE_BASE else "stub"

_MANIFEST = {
    "mode": MODE,
    "resources": [
        {"name": "current.json", "path": "/mcp/resource/current.json"},
        {"name": "last_training.json", "path": "/mcp/resource/last_training.json"},
        {"name": "schema.plan.json", "path": "/mcp/resource/schema.plan.json"},
    ],
    "tools": [
        {"name": "plan.validate", "path": "/mcp/tool/plan.validate", "method": "POST"},
        {"name": "plan.publish", "path": "/mcp/tool/plan.publish", "method": "POST"},
        {"name": "plan.delete", "path": "/mcp/tool/plan.delete", "method": "POST"},
    ],
}


def _auth_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if BRIDGE_TOKEN:
        headers["Authorization"] = f"Bearer {BRIDGE_TOKEN}"
    return headers


async def bridge_get(path: str, params: Dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=15) as cx:
        response = await cx.get(f"{BRIDGE_BASE}{path}", params=params, headers=_auth_headers())
        response.raise_for_status()
        return response.json()


async def bridge_delete(path: str, params: Dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=15) as cx:
        response = await cx.delete(f"{BRIDGE_BASE}{path}", params=params, headers=_auth_headers())
        response.raise_for_status()
        return {"ok": True}


async def bridge_post(path: str, payload: Dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=15) as cx:
        response = await cx.post(
            f"{BRIDGE_BASE}{path}",
            json=payload,
            headers={**_auth_headers(), "Content-Type": "application/json"},
        )
        response.raise_for_status()
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return {"ok": True}


def _load_links() -> Dict[str, str]:
    try:
        return json.loads(LINKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_links(data: Dict[str, str]) -> None:
    LINKS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_connection_id(req: Request, payload: Dict[str, Any]) -> Optional[str]:
    header_value = req.headers.get("x-connection-id") or req.headers.get("x-conn")
    if header_value:
        return header_value
    query_value = req.query_params.get("cid")
    if query_value:
        return query_value
    return payload.get("connection_id") if isinstance(payload, dict) else None


def _resolve_user_id(conn_id: Optional[str]) -> Optional[str]:
    if USER_ID_ENV:
        return USER_ID_ENV
    if not conn_id:
        return None
    return _load_links().get(conn_id)


def _draft_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    draft = payload.get("draft")
    if isinstance(draft, dict):
        return draft
    return {k: v for k, v in payload.items() if k != "confirm"}


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "ts": int(time.time())}


@app.get("/_/whoami")
async def whoami() -> Dict[str, Any]:
    return {
        "ok": True,
        "mode": MODE,
        "bridge_base": BRIDGE_BASE or None,
        "user_id_fallback": bool(USER_ID_ENV),
    }


@app.get("/_/link")
async def link_page() -> HTMLResponse:
    html = """
        <meta charset='utf-8'><style>body{font:14px system-ui;margin:24px;max-width:640px}</style>
        <h3>Link connection to user</h3>
        <form method="post" action="/_/link">
          <label>connection_id <input name="connection_id" required></label><br/><br/>
          <label>user_id <input name="user_id" required></label><br/><br/>
          <button type="submit">Save</button>
        </form>
        <p>Tip: set USER_ID env to bypass linking globally.</p>
        """
    return HTMLResponse(html)


@app.post("/_/link")
async def link_save(request: Request) -> Dict[str, Any]:
    form = await request.form()
    conn_id = str(form.get("connection_id") or "").strip()
    user_id = str(form.get("user_id") or "").strip()
    links = _load_links()
    if conn_id and user_id:
        links[conn_id] = user_id
        _save_links(links)
        return {"ok": True, "linked": {"connection_id": conn_id, "user_id": user_id}}
    return {"ok": False, "error": "bad_input"}


@app.get("/mcp/resource/{name}")
async def resource_get(name: str, request: Request) -> Any:
    conn_id = _resolve_connection_id(request, {})
    user_id = _resolve_user_id(conn_id)

    if name == "current.json":
        if BRIDGE_BASE and user_id:
            return await bridge_get("/bridge/current", {"user_id": user_id})
        return {"ok": True, "athlete_id": user_id or "i-demo", "week": "2025-W45", "risks": []}

    if name == "last_training.json":
        if BRIDGE_BASE and user_id:
            return await bridge_get("/bridge/last_training", {"user_id": user_id})
        return {"ok": True, "last": {"date": "2025-11-02", "type": "Run", "km": 10}}

    if name == "schema.plan.json":
        return JSONResponse(load_schema())

    return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)


@app.post("/mcp/tool/plan.validate")
async def plan_validate(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    draft = _draft_from_payload(payload)
    if not draft:
        return {"ok": False, "errors": [{"path": [], "message": "Invalid plan payload"}], "warnings": [], "diff": {}}

    errors = [
        {"path": list(error.absolute_path), "message": error.message}
        for error in PLAN_VALIDATOR.iter_errors(draft)
    ]
    return {"ok": not errors, "errors": errors, "warnings": [], "diff": {}}


@app.post("/mcp/tool/plan.publish")
async def plan_publish(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid_payload"}
    if not payload.get("confirm", False):
        return {"ok": False, "need_confirm": True, "hint": "Add confirm:true"}

    draft = _draft_from_payload(payload)
    external_id = payload.get("external_id") or draft.get("external_id") or "plan:demo"

    conn_id = _resolve_connection_id(request, payload)
    user_id = _resolve_user_id(conn_id)

    if BRIDGE_BASE:
        if not user_id:
            return {"ok": False, "need_link": True, "hint": "Open /_/link and map connection_id→user_id, or set USER_ID env"}
        try:
            await bridge_delete("/bridge/plan", {"external_id": external_id})
        except Exception:
            pass
        bridge_response = await bridge_post(
            "/bridge/plan",
            {"user_id": user_id, "external_id": external_id, "draft": draft},
        )
        return {
            "ok": True,
            "external_id": external_id,
            "days_written": len(draft.get("days", [])) if isinstance(draft.get("days"), list) else 0,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "bridge",
            "bridge": bridge_response,
        }

    return {
        "ok": True,
        "days_written": len(draft.get("days", [])) if isinstance(draft.get("days"), list) else 0,
        "external_id": external_id,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "mcp-stub",
    }


@app.post("/mcp/tool/plan.delete")
async def plan_delete(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid_payload"}
    if not payload.get("confirm", False):
        return {"ok": False, "need_confirm": True, "hint": "Add confirm:true"}

    external_id = payload.get("external_id") or ""
    conn_id = _resolve_connection_id(request, payload)
    user_id = _resolve_user_id(conn_id)

    if BRIDGE_BASE:
        if not user_id:
            return {"ok": False, "need_link": True, "hint": "Open /_/link and map connection_id→user_id, or set USER_ID env"}
        await bridge_delete("/bridge/plan", {"external_id": external_id})
        return {"ok": True, "external_id": external_id, "source": "bridge"}

    return {"ok": True, "external_id": external_id, "source": "mcp-stub"}


async def _sse_stream():
    yield {"event": "manifest", "data": json.dumps(_MANIFEST)}
    while True:
        await asyncio.sleep(2)
        yield {"event": "ping", "data": json.dumps({"ts": int(time.time())})}


@app.get("/sse")
async def sse_endpoint() -> EventSourceResponse:
    return EventSourceResponse(_sse_stream())


def main() -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
