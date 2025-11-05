# src/server.py
# STAS MCP bridge — JSON-RPC server for ChatGPT Connectors
# - Толерантен к формату arguments (dict | JSON-string | bytes, params.arguments | params.args)
# - Дедупликация tools/list (наш read-tool plan.list перекрывает старый)
# - Безопасные ошибки: JSON-RPC error с HTTP 200 (чтобы клиент не видел http_error)
# - Импорты plan.* с fallback-заглушками, если файла нет (сервис всё равно поднимется)

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Sequence

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

# ---------- пакетные импорты read/session (находятся в src/mcp/...) ----------
from .mcp import tools_read as mcp_tools_read
from .mcp import tools_session as mcp_tools_session

# ---------- plan.*: пытаемся импортировать реализацию; если нет — заглушки ----------
try:
    from .tools_plan_write_ext import (  # type: ignore
        plan_validate as _plan_validate,
        plan_publish as _plan_publish,
        plan_delete as _plan_delete,
        plan_update as _plan_update,
        plan_status as _plan_status,
        ToolError as PlanToolError,
    )
except Exception:  # noqa: BLE001 - если модуля нет или импорты упали
    class PlanToolError(Exception):
        def __init__(self, code: int = 501, message: str = "plan.* not configured"):
            super().__init__(message)
            self.code = code
            self.message = message

    async def _unavail(_: Dict[str, Any]) -> Dict[str, Any]:
        raise PlanToolError(501, "plan.* is unavailable on this instance")

    _plan_validate = _unavail
    _plan_publish = _unavail
    _plan_delete = _unavail
    _plan_update = _unavail
    _plan_status = _unavail  # type: ignore

# ---------- FastAPI ----------
app = FastAPI(title="stas-mcp-bridge", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MANIFEST_SCHEMA_URI = "http://json-schema.org/draft-07/schema#"


# ---------- helpers ----------
def rpc_ok(rpc_id: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def rpc_err(rpc_id: Any, code: int, message: str, data: Any | None = None) -> JSONResponse:
    payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
    if data is not None:
        payload["error"]["data"] = data
    return JSONResponse(payload)


def _tool_json_content(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict) and "content" in obj and isinstance(obj["content"], list):
        return obj
    return {"content": [{"type": "json", "json": obj}]}


# ---------- schemas for plan.* in tools/list ----------
def _draft_input_schema() -> Dict[str, Any]:
    return {
        "$schema": MANIFEST_SCHEMA_URI,
        "type": "object",
        "required": ["external_id", "athlete_id", "days"],
        "properties": {
            "external_id": {"type": "string", "description": "External ID of the plan"},
            "athlete_id": {"type": "string"},
            "meta": {"type": "object"},
            "days": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["date", "title", "blocks"],
                    "properties": {"date": {"type": "string"}, "title": {"type": "string"}, "blocks": {"type": "array"}},
                },
            },
        },
    }


def _plan_tool_definitions(draft_schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "name": "plan.validate",
            "description": "Validate training plan draft against schema.plan.json",
            "inputSchema": {
                "$schema": MANIFEST_SCHEMA_URI,
                "type": "object",
                "required": ["draft"],
                "properties": {"draft": draft_schema, "connection_id": {"type": "string"}},
            },
        },
        {
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
            "name": "plan.delete",
            "description": "Delete a plan by external_id; requires confirm:true",
            "inputSchema": {
                "$schema": MANIFEST_SCHEMA_URI,
                "type": "object",
                "required": ["external_id", "confirm"],
                "properties": {"external_id": {"type": "string"}, "confirm": {"type": "boolean"}, "connection_id": {"type": "string"}},
            },
        },
        {
            "name": "plan.update",
            "description": "Partially update a previously published plan. Dry-run by default.",
            "inputSchema": {
                "$schema": MANIFEST_SCHEMA_URI,
                "type": "object",
                "required": ["external_id", "patch"],
                "properties": {
                    "external_id": {"type": "string"},
                    "patch": {"type": "object"},
                    "confirm": {"type": "boolean", "default": False, "description": "Set to true to persist changes; default is dry-run."},
                    "if_match": {"type": ["string", "null"], "description": "ETag of the current plan version."},
                    "connection_id": {"type": "string"},
                },
            },
        },
        {
            "name": "plan.status",
            "description": "Fetch publication status and etag for a plan external_id.",
            "inputSchema": {
                "$schema": MANIFEST_SCHEMA_URI,
                "type": "object",
                "required": ["external_id"],
                "properties": {"external_id": {"type": "string"}, "connection_id": {"type": "string"}},
            },
        },
    ]


# ---------- merge tools for tools/list ----------
def build_tools_for_rpc() -> List[Dict[str, Any]]:
    plan_tools = _plan_tool_definitions(_draft_input_schema())
    read_tools = mcp_tools_read.get_tool_definitions()
    session_tools = mcp_tools_session.get_tool_definitions()

    merged: Dict[str, Dict[str, Any]] = {}

    def _merge(tools: Sequence[Dict[str, Any]]) -> None:
        for t in tools:
            name = (t.get("name") or t.get("id") or "").strip()
            if not name:
                continue
            merged[name] = t  # last-wins

    _merge(plan_tools)
    _merge(read_tools)      # наш plan.list перекрывает старый
    _merge(session_tools)

    return list(merged.values())


# ---------- plan proxies ----------
async def plan_validate(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await _plan_validate(arguments)


async def plan_publish(request: Request, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await _plan_publish(request, arguments)


async def plan_delete(request: Request, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await _plan_delete(request, arguments)


async def plan_update(request: Request, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await _plan_update(request, arguments)


async def plan_status(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await _plan_status(arguments)


# ---------- HTTP endpoints ----------
@app.get("/healthz")
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok", media_type="text/plain")


@app.get("/mcp")
async def mcp_manifest() -> JSONResponse:
    return JSONResponse(
        {"name": "stas-mcp-bridge", "version": "1.0.0", "endpoints": ["POST /mcp (JSON-RPC)", "GET /mcp/resource/{name}", "GET /healthz"]}
    )


@app.get("/mcp/resource/{name}")
async def resources(name: str) -> JSONResponse:
    if name == "server-info.json":
        return JSONResponse({"name": "stas-mcp-bridge", "version": "1.0.0"})
    return JSONResponse({"error": "unknown resource"}, status_code=404)


# ---------- JSON-RPC ----------
@app.post("/mcp")
async def mcp_rpc(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception as exc:  # pragma: no cover
        return rpc_err(None, -32700, "Parse error", str(exc))

    rpc_id = body.get("id")
    method = str(body.get("method") or "").strip()
    params = body.get("params") or {}

    if method == "initialize":
        return rpc_ok(
            rpc_id,
            {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"list": True, "call": True, "listChanged": True}, "resources": {"list": True, "read": True}},
                "serverInfo": {"name": "stas-mcp-bridge", "version": "1.0.0"},
            },
        )

    if method == "tools/list":
        return rpc_ok(rpc_id, {"tools": build_tools_for_rpc()})

    if method == "tools/call":
        name = str(params.get("name") or "").strip()

        # normalize arguments (dict | JSON string | bytes) from params.arguments OR params.args
        def _normalize_arguments(p: dict) -> dict:
            raw = p.get("arguments", p.get("args", {}))

            if isinstance(raw, (bytes, bytearray)):
                try:
                    raw = raw.decode("utf-8", "strict")
                except Exception:
                    pass

            if isinstance(raw, str):
                s = raw.strip()
                try:
                    return json.loads(s) if s else {}
                except Exception as exc:
                    logging.warning("tools/call: arguments JSON parse error: %s; sample=%r", exc, s[:200])
                    raise ValueError(f"arguments: invalid JSON string: {exc}")

            if isinstance(raw, dict):
                return raw

            raise ValueError(f"arguments: unsupported type {type(raw)}; expected object or JSON string")

        try:
            arguments = _normalize_arguments(params)
        except ValueError as exc:
            return rpc_err(rpc_id, -32602, "Invalid params", str(exc))

        connection_id = (
            request.headers.get("x-connection-id")
            or request.headers.get("x-conn")
            or request.query_params.get("cid")
            or arguments.get("connection_id")
        )

        try:
            if mcp_tools_session.has_tool(name):
                result = await mcp_tools_session.call_tool(name, arguments)
                return rpc_ok(rpc_id, _tool_json_content(result))

            if mcp_tools_read.has_tool(name):
                result = await mcp_tools_read.call_tool(name, arguments)
                return rpc_ok(rpc_id, result)  # already enveloped

            if name == "plan.validate":
                result = await plan_validate(arguments)
                return rpc_ok(rpc_id, _tool_json_content(result))

            if name == "plan.publish":
                payload_in = dict(arguments)
                if connection_id and not payload_in.get("connection_id"):
                    payload_in["connection_id"] = connection_id
                result = await plan_publish(request, payload_in)
                return rpc_ok(rpc_id, _tool_json_content(result))

            if name == "plan.update":
                payload_in = dict(arguments)
                if connection_id and not payload_in.get("connection_id"):
                    payload_in["connection_id"] = connection_id
                result = await plan_update(request, payload_in)
                return rpc_ok(rpc_id, _tool_json_content(result))

            if name == "plan.status":
                payload_in = dict(arguments)
                result = await plan_status(payload_in)
                return rpc_ok(rpc_id, _tool_json_content(result))

            if name == "plan.delete":
                payload_in = dict(arguments)
                if connection_id and not payload_in.get("connection_id"):
                    payload_in["connection_id"] = connection_id
                result = await plan_delete(request, payload_in)
                return rpc_ok(rpc_id, _tool_json_content(result))

            return rpc_err(rpc_id, -32601, f"Method tools/call: unknown tool '{name}'")

        except (mcp_tools_read.ToolError, PlanToolError) as exc:  # type: ignore[attr-defined]
            code = getattr(exc, "code", 424) or 424
            message = getattr(exc, "message", str(exc))
            return rpc_err(rpc_id, code, message)
        except Exception as exc:  # pragma: no cover
            logging.exception("tools/call unhandled exception: %s", exc)
            return rpc_err(rpc_id, -32000, "Tool execution error", str(exc))

    return rpc_err(rpc_id, -32601, f"Unknown method '{method}'")
