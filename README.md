# STAS MCP Bridge (stub)

- Python FastAPI service compatible with MCP Connectors.
- Endpoints: /healthz, /mcp/resource/{current.json,last_training.json,schema.plan.json}, /mcp/tool/{plan.validate,plan.publish,plan.delete}, /sse.

## Local run
```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn src.server:app --host 0.0.0.0 --port 8787
```

## Smoke checks
```bash
curl -sS http://127.0.0.1:8787/healthz
timeout 6 curl -Ns http://127.0.0.1:8787/sse | sed -n '1,20p'
```
