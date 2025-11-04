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

### Connect (ChatGPT → Connector)

1. MCP Server URL: `https://stas-mcp.onrender.com/sse`
2. Authentication: none required
3. Press **Connect**. On first use you will be redirected to [`/link`](https://stas-mcp.onrender.com/link) to enter your STAS `user_id` (one time per connection).

After the `user_id` is stored, the connector will call the MCP resources/tools using the linked identity.

### What endpoints MCP calls

Gateway base: `https://intervals.stas.run/gw`

* `GET /gw/api/db/user_summary` → `/mcp/resource/current.json`
* `GET /gw/trainings?oldest=…&newest=…` → `/mcp/resource/last_training.json`
* `POST /gw/icu/events?external_id_prefix=plan:` → `/mcp/tool/plan.publish`
* `DELETE /gw/icu/events?...` → `/mcp/tool/plan.delete`

UAT (production evidence): see [UAT_PROD.md](UAT_PROD.md).

### Diagnostics

* [`/whoami`](https://stas-mcp.onrender.com/whoami) or [`/_/whoami`](https://stas-mcp.onrender.com/_/whoami)
* [`/link`](https://stas-mcp.onrender.com/link) (with `?connection_id=` to pre-fill)
