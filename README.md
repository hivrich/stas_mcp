# STAS MCP Bridge (stub)

- Python FastAPI service compatible with MCP Connectors.
- Endpoints: /healthz, /mcp/resource/{current.json,last_training.json,schema.plan.json}, /mcp/tool/{plan.validate,plan.publish,plan.delete}, /sse.

## Quickstart

* Run `uvicorn src.server:app --reload`
* Open https://stas-mcp.onrender.com/_/sse to visually watch manifest + ping.

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
The server listens on port `8787` by default and exposes the MCP endpoints
under `/mcp` as well as `/sse` for streaming updates.

## UAT Evidence

The latest local user acceptance test run is documented in
[UAT.md](UAT.md).
## Deploy (Render)
1. Create an account at https://render.com (free plan is OK).
2. Click **New → Web Service**, connect your GitHub, and select this repository.
3. For Environment = **Python**, set:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn src.server:app --host 0.0.0.0 --port $PORT`
4. After deploy, open the URL and check:
   - `/healthz` returns JSON
   - `/sse` shows `event: manifest` then `event: ping`

## Live service

Base URL: [https://stas-mcp.onrender.com](https://stas-mcp.onrender.com)

Quick checks:

* Health: [https://stas-mcp.onrender.com/healthz](https://stas-mcp.onrender.com/healthz)
* SSE stream: [https://stas-mcp.onrender.com/sse](https://stas-mcp.onrender.com/sse)  (first event: `manifest`, then periodic `ping`)

UAT (production evidence): see [UAT_PROD.md](UAT_PROD.md).

