# src/server.py
from __future__ import annotations
import json
from typing import Any, Dict, Tuple, List, Callable, Awaitable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# импортируем реализации тулов
from src.mcp.tools_read import user_summary_fetch, user_last_training_fetch
from src.mcp.tools_plan import (
    plan_list, plan_status, plan_update, plan_publish, plan_delete, plan_validate
)

app = FastAPI(title="STAS MCP Server", version="2025.11.05")

# ---------------- helpers ----------------

Json = Dict[str, Any]
AsyncTool = Callable[[Dict[str, Any]], Awaitable[Tuple[Json, str]]]

def _rpc_ok(id_: Any, payload: Json) -> Json:
    return {"jsonrpc": "2.0", "id": id_, "result": payload}

def _content(json_payload: Json, text: str) -> Json:
    # Надёжный формат контента для ChatGPT MCP: structured в type:"json" + подпись
    return {"content": [
        {"type": "json", "json": json_payload},
        {"type": "text", "text": text},
    ]}

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

def _okify(payload: Json) -> Json:
    # гарантируем наличие ok-флага, чтобы клиент мог полагаться на него
    return payload if "ok" in payload else {"ok": True, **payload}

# ---------------- registry ----------------
# Публикуем ИМЕНА БЕЗ ТОЧЕК (snake_case)
TOOLS: Dict[str, AsyncTool] = {
    "user_summary_fetch": user_summary_fetch,
    "user_last_training_fetch": user_last_training_fetch,
    "plan_list":     plan_list,
    "plan_status":   plan_status,
    "plan_update":   plan_update,
    "plan_publish":  plan_publish,
    "plan_delete":   plan_delete,
    "plan_validate": plan_validate,
}
# Принимаем легаси-алиасы с точками (совместимость вызовов)
ALIASES: Dict[str, str] = {
    "user.summary.fetch": "user_summary_fetch",
    "user.last_training.fetch": "user_last_training_fetch",
    "plan.list": "plan_list",
    "plan.status": "plan_status",
    "plan.update": "plan_update",
    "plan.publish": "plan_publish",
    "plan.delete": "plan_delete",
    "plan.validate": "plan_validate",
}

# JSON Schema для входов. Публикуем ОДНУ схему под ДВУМЯ ключами
# (inputSchema — как в доке OpenAI; input_schema — для совместимости некоторых билдеров).
BASE_OBJ: Json = {"type": "object"}

def both_keys(schema_obj: Json) -> Json:
    return {"inputSchema": schema_obj, "input_schema": schema_obj}

# ---------------- schemas (write-действия требуют confirm:true) ----------------
TOOLS_SCHEMAS: List[Json] = [
    {
        "name": "user_summary_fetch",
        "description": "Fetch user summary (linked account or explicit user_id).",
        **both_keys({
            **BASE_OBJ,
            "properties": {
                "user_id": {"type": "integer", "description": "Optional explicit user id."},
                "connection_id": {"type": "string", "description": "Optional chat connection id."},
            },
            "required": [],
            "additionalProperties": False,
        }),
    },
    {
        "name": "user_last_training_fetch",
        "description": "Return recent trainings in a date window.",
        **both_keys({
            **BASE_OBJ,
            "properties": {
                "user_id": {"type": "integer"},
                "oldest": {"type": "string", "description": "YYYY-MM-DD"},
                "newest": {"type": "string", "description": "YYYY-MM-DD"},
                "connection_id": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        }),
    },
    {
        "name": "plan_list",
        "description": "List workout plan events for a given window.",
        **both_keys({
            **BASE_OBJ,
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
        }),
    },

    # ---- WRITE actions (dangerous): require confirm:true ----
    {
        "name": "plan_update",
        "description": "Update plan entities (dangerous; requires confirm:true).",
        **both_keys({
            **BASE_OBJ,
            "properties": {
                "patch": {"type": "object"},
                "confirm": {"type": "boolean", "const": True, "description": "Must be true to proceed."},
                "reason": {"type": "string", "description": "Why this change is needed (for audit)."}
            },
            "required": ["patch", "confirm"],
            "additionalProperties": False,
        }),
    },
    {
        "name": "plan_publish",
        "description": "Publish pending changes (dangerous; requires confirm:true).",
        **both_keys({
            **BASE_OBJ,
            "properties": {
                "note": {"type": "string"},
                "confirm": {"type": "boolean", "const": True, "description": "Must be true to proceed."}
            },
            "required": ["confirm"],
            "additionalProperties": False,
        }),
    },
    {
        "name": "plan_delete",
        "description": "Delete a plan item (dangerous; requires confirm:true).",
        **both_keys({
            **BASE_OBJ,
            "properties": {
                "id": {"type": "string"},
                "confirm": {"type": "boolean", "const": True, "description": "Must be true to proceed."}
            },
            "required": ["id", "confirm"],
            "additionalProperties": False,
        }),
    },

    # ---- SAFE misc ----
    {"name":"plan_status",  "description":"Get current plan status",   **both_keys({"type":"object"})},
    {"name":"plan_validate","description":"Validate plan consistency", **both_keys({"type":"object"})},
]

# ---------------- health ----------------

@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "stas-mcp", "version": app.version}

@app.get("/sse")
async def sse_stub():
    return JSONResponse({"ok": True, "sse": "noop"})

# ---------------- MCP endpoint (JSON-RPC 2.0) ----------------

@app.post("/mcp")
async def mcp(request: Request):
    """
    Обязательные методы MCP:
      - initialize
      - tools/list
      - tools/call
    Никогда не роняем транспорт: любые ошибки → валидный JSON-RPC с result.content.
    """
    body = await request.json()
    id_ = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    try:
        if method == "initialize":
            # оф. гайд OpenAI: protocolVersion + capabilities.tools + serverInfo
            return _rpc_ok(id_, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "stas-mcp", "version": app.version},
            })

        if method == "tools/list":
            return _rpc_ok(id_, {"tools": TOOLS_SCHEMAS})

        if method == "tools/call":
            name_in = params.get("name")
            raw_args = params.get("arguments")
            args, was_string = _args_to_obj(raw_args)

            # нормализуем имя: snake_case каноника или легаси с точками
            name = ALIASES.get(name_in, name_in)
            handler: AsyncTool | None = TOOLS.get(name)

            if not handler:
                return _rpc_ok(id_, _content(
                    {"ok": False, "error": {"code": "tool_not_found", "name": name_in}},
                    f"{name_in}: error"
                ))

            try:
                payload, text = await handler(args)
                payload = _okify(payload)
                return _rpc_ok(id_, _content(payload, text or f"{name}: ok"))
            except Exception as e:
                return _rpc_ok(id_, _content(
                    {"ok": False, "error": {
                        "code": "internal_error",
                        "message": str(e)[:500],
                        "tool": name,
                        "args_were_string": was_string
                    }},
                    f"{name}: error"
                ))

        # неизвестный метод — мягкий ответ (валидный JSON-RPC)
        return _rpc_ok(id_, _content(
            {"ok": False, "error": {"code": "method_not_supported", "method": method}},
            "unsupported"
        ))
    except Exception as e:
        # «последний заслон» от 4xx/5xx
        return _rpc_ok(id_, _content(
            {"ok": False, "error": {"code": "fatal", "message": str(e)[:500]}},
            "fatal"
        ))
