# src/server.py
# FastAPI MCP endpoint: JSON-RPC 2.0 (initialize, tools/list, tools/call)
# Ключевые гарантии:
#  - Никогда не роняем транспорт (никаких 424/500 наружу)
#  - Всегда возвращаем result.content=[{type:"json"},{type:"text"}]
#  - Принимаем arguments как dict ИЛИ как строку JSON
#  - tools/list описывает схемы входа/выхода

from __future__ import annotations
import json
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .mcp.tools_read import user_summary_fetch, user_last_training_fetch
from .mcp.tools_plan import (
    plan_list, plan_status, plan_update, plan_publish, plan_delete, plan_validate
)

app = FastAPI(title="STAS MCP Server", version="2025.11.05")

# ------------------------ MCP helpers ------------------------

def _rpc_ok(id_: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": payload}

def _content(json_payload: Dict[str, Any], text: str) -> Dict[str, Any]:
    return {
        "content": [
            {"type": "json", "json": json_payload},
            {"type": "text", "text": text},
        ]
    }

def _args_to_obj(arguments: Any) -> Tuple[Dict[str, Any], bool]:
    """
    Принимаем dict или JSON-строку. Возвращаем (dict, was_string).
    При любой ошибке парсинга — пустой dict и was_string=True.
    """
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
    # Гарантируем наличие ключа ok
    return payload if "ok" in payload else {"ok": True, **payload}

# ------------------------ Tool registry ------------------------

# Каждое имя → async handler(arguments: dict) -> (json_payload: dict, text: str)
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

# Для tools/list: краткие схемы (MCP Tools Spec). Упор на вход.
TOOLS_SCHEMAS = {
    "user.summary.fetch": {
        "name": "user.summary.fetch",
        "description": "Fetches user summary (linked account or explicit user_id).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "Explicit user id (optional if linked)."},
                "connection_id": {"type": "string", "description": "Chat connection id to use link storage."},
            },
            "required": [],  # допускаем пустые args
            "additionalProperties": False,
        },
    },
    "user.last_training.fetch": {
        "name": "user.last_training.fetch",
        "description": "Returns recent trainings in a date window.",
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
    # --- планы (минимально необходимые поля) ---
    "plan.list": {
        "name": "plan.list",
        "description": "List workout plan events for a given window.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "oldest": {"type": "string", "description": "YYYY-MM-DD"},
                "newest": {"type": "string", "description": "YYYY-MM-DD"},
                "category": {"type": "string", "enum": ["WORKOUT", "RECOVERY", "OTHER"]},
                "user_id": {"type": "integer"},
                "connection_id": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "plan.status": {"name": "plan.status", "description": "Get current plan status", "inputSchema": {"type": "object", "properties": {}, "required": []}},
    "plan.update": {"name": "plan.update", "description": "Update plan entities", "inputSchema": {"type": "object", "properties": {"patch": {"type": "object"}}, "required": ["patch"]}},
    "plan.publish": {"name": "plan.publish", "description": "Publish pending plan changes", "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": []}},
    "plan.delete": {"name": "plan.delete", "description": "Delete a plan item", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    "plan.validate": {"name": "plan.validate", "description": "Validate plan for consistency", "inputSchema": {"type": "object", "properties": {}, "required": []}},
}

# ------------------------ Health endpoints ------------------------

@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "stas-mcp", "version": app.version}

@app.get("/sse")
async def sse_stub():
    # простой no-op для "живости" стрима
    return JSONResponse({"ok": True, "sse": "noop"})

# ------------------------ MCP endpoint ------------------------

@app.post("/mcp")
async def mcp(request: Request):
    """
    Единая точка MCP (JSON-RPC 2.0). Поддерживает:
      - initialize
      - tools/list
      - tools/call
    Контракт: никогда не бросаем HTTP-ошибок; любые внутренние ошибки → ok:false в content[0].json.
    """
    body = await request.json()
    id_ = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    try:
        if method == "initialize":
            # Спецификация MCP (OpenAI Apps SDK / MCP Tools spec) ожидает корректный ответ JSON-RPC. 
            # Мы не роняемся и возвращаем минимальные сведения.
            return _rpc_ok(id_, {"capabilities": {"tools": True}, "meta": {"server": "stas-mcp"}})

        if method == "tools/list":
            tools = [TOOLS_SCHEMAS[name] for name in TOOLS_REGISTRY.keys()]
            return _rpc_ok(id_, {"tools": tools})

        if method == "tools/call":
            name = params.get("name")
            raw_args = params.get("arguments")
            args, was_string = _args_to_obj(raw_args)

            handler = TOOLS_REGISTRY.get(name)
            if not handler:
                payload = {"ok": False, "error": {"code": "tool_not_found", "message": f"Unknown tool: {name}"}}
                return _rpc_ok(id_, _content(payload, f"{name}: error"))

            try:
                json_payload, text = await handler(args)
                json_payload = _okify(json_payload)
                return _rpc_ok(id_, _content(json_payload, text or f"{name}: ok"))
            except Exception as e:
                # catch-all: никогда не отдаём JSON-RPC error наружу
                err = {
                    "ok": False,
                    "error": {
                        "code": "internal_error",
                        "message": str(e)[:500],
                        "args_were_string": was_string,
                        "tool": name,
                    },
                }
                return _rpc_ok(id_, _content(err, f"{name}: error"))

        # Неподдерживаемый метод — мягкий ответ
        return _rpc_ok(id_, _content({"ok": False, "error": {"code": "method_not_supported", "method": method}}, "unsupported"))
    except Exception as e:
        # Абсолютный последний заслон от 5xx/424
        return _rpc_ok(id_, _content({"ok": False, "error": {"code": "fatal", "message": str(e)[:500]}}, "fatal"))
