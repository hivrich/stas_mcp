# src/server.py
from __future__ import annotations
import json
from typing import Any, Dict, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# абсолютные импорты из пакета src
from src.mcp.tools_read import user_summary_fetch, user_last_training_fetch
from src.mcp.tools_plan import (
    plan_list, plan_status, plan_update, plan_publish, plan_delete, plan_validate
)

APP_PROTOCOL = "2025-06-18"  # MCP spec revision we speak

app = FastAPI(title="STAS MCP Server", version="2025.11.05")

# ----------------- helpers -----------------

def _rpc_ok(id_: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": payload}

def _content(text: str, structured: Dict[str, Any] | None = None, is_error: bool = False) -> Dict[str, Any]:
    res = {
        "content": [{"type": "text", "text": text}],
        "isError": bool(is_error),
    }
    if structured is not None:
        # MCP: structured data goes here (NOT as {type:"json"} in content)
        res["structuredContent"] = structured
    return res

def _args_to_obj(arguments: Any) -> Tuple[Dict[str, Any], bool]:
    if arguments is None:
        return {}, False
    if isinstance(arguments, dict):
        return arguments, False
    if isinstance(arguments, str):
        try:
            return json.loads(arguments or "{}"), True
        except Exception:
            return {}, True
    return {}, False

# ----------------- registry -----------------

TOOLS_REGISTRY = {
    "user.summary.fetch": user_summary_fetch,
    "user.last_training.fetch": user_last_training_fetch,
    "plan.list":     plan_list,
    "plan.status":   plan_status,
    "plan.update":   plan_update,
    "plan.publish":  plan_publish,
    "plan.delete":   plan_delete,
    "plan.validate": plan_validate,
}

TOOLS_SCHEMAS = {
    "user.summary.fetch": {
        "name": "user.summary.fetch",
        "description": "Fetch user summary (linked account or explicit user_id).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "connection_id": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "user.last_training.fetch": {
        "name": "user.last_training.fetch",
        "description": "Return recent trainings in a date window.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "oldest": {"type": "string", "description": "YYYY-MM-DD"},
                "newest": {"type": "string", "description": "YYYY-MM-DD"},
                "connection_id": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "plan.list": {
        "name": "plan.list",
        "description": "List workout plan events for a given window.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "oldest": {"type": "string"},
                "newest": {"type": "string"},
                "category": {"type": "string", "enum": ["WORKOUT", "RECOVERY", "OTHER"]},
                "user_id": {"type": "integer"},
                "connection_id": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "plan.status":   {"name": "plan.status",   "description": "Get current plan status",   "inputSchema": {"type": "object","properties": {},"required": []}},
    "plan.update":   {"name": "plan.update",   "description": "Update plan entities",      "inputSchema": {"type": "object","properties": {"patch": {"type": "object"}},"required": ["patch"]}},
    "plan.publish":  {"name": "plan.publish",  "description": "Publish pending changes",   "inputSchema": {"type": "object","properties": {"note": {"type": "string"}},"required": []}},
    "plan.delete":   {"name": "plan.delete",   "description": "Delete a plan item",        "inputSchema": {"type": "object","properties": {"id": {"type": "string"}},"required": ["id"]}},
    "plan.validate": {"name": "plan.validate", "description": "Validate plan consistency", "inputSchema": {"type": "object","properties": {},"required": []}},
}

# ----------------- health -----------------

@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "stas-mcp", "version": app.version, "protocol": APP_PROTOCOL}

@app.get("/sse")
async def sse_stub():
    return JSONResponse({"ok": True, "sse": "noop"})

# ----------------- MCP endpoint -----------------

@app.post("/mcp")
async def mcp(request: Request):
    """
    MCP over JSON-RPC 2.0.
    MUST support: initialize, tools/list, tools/call.
    MUST: never break transport; errors go via result.isError/structuredContent per spec.
    """
    body = await request.json()
    id_ = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    try:
        if method == "initialize":
            # spec: declare capabilities.tools + (optionally) echo protocolVersion
            return _rpc_ok(id_, {
                "protocolVersion": APP_PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "meta": {"server": "stas-mcp"}
            })

        if method == "tools/list":
            tools = [TOOLS_SCHEMAS[name] for name in TOOLS_REGISTRY.keys()]
            return _rpc_ok(id_, {"tools": tools})

        if method == "tools/call":
            name = params.get("name")
            raw_args = params.get("arguments")
            args, was_string = _args_to_obj(raw_args)

            handler = TOOLS_REGISTRY.get(name)
            if not handler:
                return _rpc_ok(id_, _content(
                    text=f"{name}: unknown tool",
                    structured={"code": "tool_not_found", "name": name},
                    is_error=True,
                ))

            try:
                # handlers return (json_payload: dict, text: str)
                json_payload, text = await handler(args)
                return _rpc_ok(id_, _content(
                    text=text or f"{name}: ok",
                    structured=json_payload,
                    is_error=bool(json_payload.get("ok") is False)
                ))
            except Exception as e:
                return _rpc_ok(id_, _content(
                    text=f"{name}: error",
                    structured={"code": "internal_error", "message": str(e)[:500], "args_were_string": was_string, "tool": name},
                    is_error=True
                ))

        # unknown method → soft error (still valid JSON-RPC result)
        return _rpc_ok(id_, _content(
            text="unsupported",
            structured={"code": "method_not_supported", "method": method},
            is_error=True
        ))
    except Exception as e:
        # last resort: never 4xx/5xx to client
        return _rpc_ok(id_, _content(
            text="fatal",
            structured={"code": "fatal", "message": str(e)[:500]},
            is_error=True
        ))
