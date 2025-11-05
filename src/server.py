# src/server.py
**BASE_OBJ,
"properties": {
"id": {"type": "string"},
"confirm": {"type": "boolean", "const": True, "description": "Must be true to proceed."}
},
"required": ["id", "confirm"],
"additionalProperties": False,
}),
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
return _rpc_ok(id_, {
"protocolVersion": "2024-11-05",
"capabilities": {"tools": {"listChanged": False}},
"serverInfo": {"name": "stas-mcp", "version": app.version},
})


if method == "tools/list":
# если нет Auth — скрываем write-инструменты, публикуем только READ
tools = [t for t in TOOLS_SCHEMAS_ALL if t["name"] in READ_ONLY or has_auth]
return _rpc_ok(id_, {"tools": tools})


if method == "tools/call":
name_in = params.get("name")
raw_args = params.get("arguments")
args, was_string = _args_to_obj(raw_args)


name = ALIASES.get(name_in, name_in)
# блокируем write-вызовы без Auth
if (name in WRITE_ONLY) and not has_auth:
return _rpc_ok(id_, _content(
{"ok": False, "error": {"code": "auth_required", "message": "write tools require Authorization"}},
f"{name}: error"
))


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


return _rpc_ok(id_, _content(
{"ok": False, "error": {"code": "method_not_supported", "method": method}},
"unsupported"
))
except Exception as e:
return _rpc_ok(id_, _content(
{"ok": False, "error": {"code": "fatal", "message": str(e)[:500]}},
"fatal"
))
