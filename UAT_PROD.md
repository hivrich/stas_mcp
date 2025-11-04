# UAT Evidence (PROD)
BASE: https://stas-mcp.onrender.com
DATE: 2025-11-04

HEALTHZ:
```json
{"ok":true,"ts":1762252179}
```

SSE(HEAD):

```
event: manifest
data: {"resources": [{"name": "current.json", "path": "/mcp/resource/current.json"}, {"name": "last_training.json", "path": "/mcp/resource/last_training.json"}, {"name": "schema.plan.json", "path": "/mcp/resource/schema.plan.json"}], "tools": [{"name": "plan.validate", "path": "/mcp/tool/plan.validate", "method": "POST"}, {"name": "plan.publish", "path": "/mcp/tool/plan.publish", "method": "POST"}, {"name": "plan.delete", "path": "/mcp/tool/plan.delete", "method": "POST"}]}

event: ping
data: {"ts": 1762252213}
```

NOTES:

* Codex runner cannot access external URLs (CONNECT 403), so PROD checks were captured by a human via browser and recorded here.
* Live endpoints: `/healthz`, `/mcp/resource/schema.plan.json`, `/mcp/tool/*`, `/sse`.
* For full request flows (validate → publish(confirm) → delete), use any HTTP client against BASE.

```
