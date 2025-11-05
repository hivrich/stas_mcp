# src/server.py
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, List, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ваши реализации тулов
from src.mcp.tools_read import user_summary_fetch, user_last_training_fetch
from src.mcp.tools_plan import (
    plan_list,
    plan_status,
    plan_update,
    plan_publish,
    plan_delete,
    plan_validate,
)

app = FastAPI(title="STAS MCP Server", version="2025.11.05")

# ---------------- helpers ----------------
Json = Dict[str, Any]
AsyncTool = Callable[[Dict[str, Any]], Awaitable[Tuple[Json, str]]]


def _rpc_ok(id_: Any, payload: Json) -> Json:
    return {"jsonrpc": "2.0", "id": id_, "result": payload}


def _content(json_payload: Json, text: str) -> Json:
    # контент для ChatGPT MCP: structured в type:"json" + подпись
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


def _okify(payload: Json) -> Json:
    return payload if "ok" in payload else {"ok": True, **payload}


# ---------------- registry ----------------
TOOLS: Dict[str, AsyncTool] = {
    # READ/SAFE
    "user_summary_fetch": user_summary_fetch,
    "user_last_training_fetch": user_last_training_fetch,
    "plan_list": plan_list,
    "plan_status": plan_status,
    "plan_validate": plan_validate,
    # WRITE/DANGEROUS (только при наличии Authorization)
    "plan_update": plan_update,
    "plan_publish": plan_publish,
    "plan_delete": plan_delete,
}

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

READ_ONLY = {
    "user_summary_fetch",
    "user_last_training_fetch",
    "plan_list",
    "plan_status",
    "plan_validate",
}
WRITE_ONLY = {"plan_update", "plan_publish", "plan_delete"}

BASE_OBJ: Json = {"type": "object"}


def both_keys(schema_obj: Json) -> Json:
    # публикуем схему под двумя ключами (совместимость разных билдов)
    return {"inputSchema": schema_obj, "input_schema": schema_obj}


TOOLS_SCHEMAS_ALL: List[Json] = [
    {
        "name": "user_summary_fetch",
        "description": "Fetch user summary (linked account or explicit user_id).",
        **both_keys(
            {
                **BASE_OBJ,
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "Optional explicit user id.",
                    },
                    "connection_id": {
                        "type": "string",
                        "description": "Optional chat connection id.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            }
        ),
    },
    {
        "name": "user_last_training_fetch",
        "description": "Return recent trainings in a date window.",
        **both_keys(
            {
                **BASE_OBJ,
                "properties": {
                    "user_id": {"type": "integer"},
                    "oldest": {"type": "string", "description": "YYYY-MM-DD"},
                    "newest": {"type": "string", "description": "YYYY-MM-DD"},
                    "connection_id": {"type": "string"},
                },
                "required": [],
                "additionalProperties": False,
            }
        ),
    },
    {
        "name": "plan_list",
        "description": "List workout plan events for a given window.",
        **both_keys(
            {
                **BASE_OBJ,
                "properties": {
                    "oldest": {"type": "string", "description": "YYYY-MM-DD"},
                    "newest": {"type": "string", "description": "YYYY-MM-DD"},
                    "category": {
                        "type": "string",
                        "enum": ["WORKOUT", "RECOVERY", "OTHER"],
                    },
                    "user_id": {"type": "integer"},
                    "connection_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": [],
                "additionalProperties": False,
            }
        ),
    },
    {"name": "plan_status", "description": "Get current plan status", **both_keys({"type": "object"})},
    {"name": "plan_validate", "description": "Validate plan consistency", **both_keys({"type": "object"})},
    # WRITE (покажем только при Authorization)
    {
        "name": "plan_update",
        "description": "Update plan entities (dangerous; requires confirm:true and Authorization).",
        **both_keys(
            {
                **BASE_OBJ,
                "properties": {
                    "patch": {"type": "object"},
                    "confirm": {
                        "type": "boolean",
                        "const": True,
                        "description": "Must be true to proceed.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this change is needed (for audit).",
                    },
                },
                "required": ["patch", "confirm"],
                "additionalProperties": False,
            }
        ),
    },
    {
        "name": "plan_publish",
        "description": "Publish pending changes (dangerous; requires confirm:true and Authorization).",
        **both_keys(
            {
                **BASE_OBJ,
                "properties": {
                    "note": {"type": "string"},
                    "confirm": {
                        "type": "boolean",
                        "const": True,
                        "description": "Must be true to proceed.",
                    },
                },
                "required": ["confirm"],
                "additionalProperties": False,
            }
        ),
    },
    {
        "name": "plan_delete",
        "description": "Delete a plan item (dangerous; requires confirm:true and Authorization).",
        **both_keys(
            {
                **BASE_OBJ,
                "properties": {
                    "id": {"type": "string"},
                    "confirm": {
                        "type": "boolean",
                        "const": True,
                        "description": "Must be true to proceed.",
                    },
                },
                "required": ["id", "confirm"],
                "additionalProperties": False,
            }
        ),
    },
]

# ---------------- health ----------------
@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "stas-mcp", "version": app.version}


@app.get("/sse")
async def sse_stub():
    return JSONResponse({"ok": True, "sse": "noop"})


# ---------------- MCP endpoint ----------------
@app.post("/mcp")
async def mcp(request: Request):
    body = await request.json()
    id_ = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    # наличие любого Authorization-заголовка считаем признаком аутентификации
    has_auth = bool(request.headers.get("authorization"))

    try:
        if method == "initialize":
            return _rpc_ok(
                id_,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "stas-mcp", "version": app.version},
                },
            )

        if method == "tools/list":
            # без Auth — скрываем write-инструменты, публикуем только READ
            tools = [t for t in TOOLS_SCHEMAS_ALL if t["name"] in READ_ONLY or has_auth]
            return _rpc_ok(id_, {"tools": tools})

        if method == "tools/call":
            name_in = params.get("name")
            raw_args = params.get("arguments")
            args, was_string = _args_to_obj(raw_args)

            name = ALIASES.get(name_in, name_in)

            # блокируем write-вызовы без Auth
            if (name in WRITE_ONLY) and not has_auth:
                return _rpc_ok(
                    id_,
                    _content(
                        {
                            "ok": False,
                            "error": {
                                "code": "auth_required",
                                "message": "write tools require Authorization",
                            },
                        },
                        f"{name}: error",
                    ),
                )

            handler: AsyncTool | None = TOOLS.get(name)
            if not handler:
                return _rpc_ok(
                    id_,
                    _content(
                        {"ok": False, "error": {"code": "tool_not_found", "name": name_in}},
                        f"{name_in}: error",
                    ),
                )

            try:
                payload, text = await handler(args)
                payload = _okify(payload)
                return _rpc_ok(id_, _content(payload, text or f"{name}: ok"))
            except Exception as e:
                return _rpc_ok(
                    id_,
                    _content(
                        {
                            "ok": False,
                            "error": {
                                "code": "internal_error",
                                "message": str(e)[:500],
                                "tool": name,
                                "args_were_string": was_string,
                            },
                        },
                        f"{name}: error",
                    ),
                )

        return _rpc_ok(
            id_,
            _content(
                {"ok": False, "error": {"code": "method_not_supported", "method": method}},
                "unsupported",
            ),
        )
    except Exception as e:
        return _rpc_ok(
            id_,
            _content(
                {"ok": False, "error": {"code": "fatal", "message": str(e)[:500]}},
                "fatal",
            ),
        )
