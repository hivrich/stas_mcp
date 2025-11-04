# stas_mcp

This repository provides a minimal MCP-compatible FastAPI bridge exposing
health, resources, tools, and an SSE feed.

## Quickstart

Run locally with Python 3.11+:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/server.py --host 0.0.0.0 --port 8787
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
