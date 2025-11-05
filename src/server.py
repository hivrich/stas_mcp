# src/server.py
from __future__ import annotations
import json
from typing import Any, Dict, Tuple, List

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# абсолютные импорты под текущую структуру
from src.mcp.tools_read import user_summary_fetch, user_last_training_fetch
from src.mcp.tools_plan import (
    plan_list, plan_status, plan_update, plan_publish, plan_delete, plan_validate
)

app = FastAPI(title="STAS MCP Server", version="2025.11.05")

# ---------------- helpers ----------------

def _rpc_ok(id_: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": payload}

def _content(json_payload: Dict[str, Any], text: str) -> Dict[str, Any]:
    # Надёжный формат для ChatGPT MCP: контент = [{type:"json"}, {type:"text"}]
    return {
        "content": [
            {"type": "json", "json": json_payload},
            {"type": "text", "text": text},
        ]
    }

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

def _okify(payload: Dict[str, Any]) -> Dict[str, Any]:
    return payload if "ok" in payload else {"ok": True, **payload}

# ---------------- registry ----------------

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

# ВАЖНО: MCP ждёт **input_schema** (snake_case), а не inputSchema
JSON_SCHEMA_HDR = {"$schema": "https://json-schema.org/draft-07/schema#", "type": "object"}

TOOLS_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "user.summary.fetch",
        "description": "Fetch user summary (linked account or explicit user_id).",
        "input_schema": {
            **JSON_SCHEMA_HDR,
            "properties": {
                "user_id": {"type": "integer", "description": "Explicit user id (optional)."},
                "connection_id": {"type": "string", "description": "Chat connection id (optional)."},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "user.last_training.fetch",
        "description": "Return recent trainings in a date window.",
        "input_schema": {
            **JSON_SCHEMA_HDR,
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
    {
        "name": "plan.list",
        "description": "List workout plan events for a given window.",
        "input_schema": {
            **JSON_SCHEMA_HDR,
            "properties": {
                "oldest": {"type": "string", "description": "YYYY-MM-DD"},
                "newest": {"type": "string", "description": "YYYY-MM-DD"},
                "category": {"type": "string", "enum": ["WORKOUT", "RECOVERY", "OTHER"]},
                "user_id": {"type": "integer"},
                "connection_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {"name":"plan.status","description":"Get current plan status","input_schema":{**JSON_SCHEMA_HDR,"properties":{},"required":[],"additionalProperties":False}},
    {"name":"plan.update","description":"Update plan entities","input_schema":{**JSON_SCHEMA_HDR,"properties":{"patch":{"type":"object"}},"required":["patch"],"additionalProperties":False}},
    {"name":"plan.publish","description":"Publish pending changes","input_schema":{**JSON_SCHEMA_HDR,"properties":{"note":{"type":"string"}},"required":[],"additionalProperties":False}},
    {"name":"plan.delete","description":"Delete a plan item","input_schema":{**JSON_SCHEMA_HDR,"properties":{"id":{"type":"string"}},"required":["id"],"additionalProperties":False}},
    {"name":"plan.validate","description":"Validate plan consistency","input_schema":{**JSON_SCHEMA_HDR,"properties":{},"required":[],"additionalProperties":False}},
]

# ---------------- health ----------------

@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "stas-mcp", "version": app.version}

@app.get("/sse")
async def sse_stub():
    return JSONResponse({"ok": True, "sse": "noop"})

# ---------------- MCP (JSON-RPC 2.0) ----------------

@app.post("/mcp")
async def mcp(request: Request):
    """
    Поддерживаем:
      - initialize
      - tools/list
      - tools/call
    Никогда не роняем транспорт: исключения → успешный JSON-RPC с content=[{json},{text}]
    """
    body = await request.json()
    id_ = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    try:
        if method == "initialize":
            # Минимум, который нужен ChatGPT для «Build actions…»
            return _rpc_ok(id_, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "meta": {"server": "stas-mcp"}
            })

        if method == "tools/list":
            return _rpc_ok(id_, {"tools": TOOLS_SCHEMAS})

        if method == "tools/call":
            name = params.get("name")
            raw_args = params.get("arguments")
            args, was_string = _args_to_obj(raw_args)

            handler = TOOLS_REGISTRY.get(name)
            if not handler:
                return _rpc_ok(id_, _content(
                    {"ok": False, "error": {"code": "tool_not_found", "name": name}},
                    f"{name}: error"
                ))

            try:
                payload, text = await handler(args)
                payload = _okify(payload)
                return _rpc_ok(id_, _content(payload, text or f"{name}: ok"))
            except Exception as e:
                return _rpc_ok(id_, _content(
                    {"ok": False, "error": {"code": "internal_error", "message": str(e)[:500], "args_were_string": was_string, "tool": name}},
                    f"{name}: error"
                ))

        # Неподдерживаемый метод — мягко
        return _rpc_ok(id_, _content({"ok": False, "error": {"code": "method_not_supported", "method": method}}, "unsupported"))
    except Exception as e:
        # Последний заслон от 4xx/5xx
        return _rpc_ok(id_, _content({"ok": False, "error": {"code": "fatal", "message": str(e)[:500]}}, "fatal"))
