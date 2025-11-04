# UAT Evidence
PORT: 8787
HEALTHZ:
{"ok": true, "ts": 1762248314}
SCHEMA(10):
{"type": "object", "required": ["external_id", "athlete_id", "days"], "properties": {"external_id": {"type": "string"}, "athlete_id": {"type": "string"}, "meta": {"type": "object"}, "days": {"type": "array", "items": {"type": "object", "required": ["date", "title", "blocks"], "properties": {"date": {"type": "string"}, "title": {"type": "string"}, "blocks": {"type": "array"}}}}}}
VALIDATE:
{"ok": true, "errors": [], "warnings": [], "diff": {}}
PUBLISH(no-confirm):
{"ok": false, "need_confirm": true, "hint": "Add confirm:true"}
PUBLISH(confirm):
{"ok": true, "external_id": "plan:2025-w45", "status": "published", "days_written": 1}
DELETE(confirm):
{"ok": true, "external_id": "plan:2025-w45"}
SSE(HEAD):
event: manifest
data: {"resources": [{"name": "current.json", "path": "/mcp/resource/current.json"}, {"name": "last_training.json", "path": "/mcp/resource/last_training.json"}, {"name": "schema.plan.json", "path": "/mcp/resource/schema.plan.json"}], "tools": [{"name": "plan.validate", "path": "/mcp/tool/plan.validate", "method": "POST"}, {"name": "plan.publish", "path": "/mcp/tool/plan.publish", "method": "POST"}, {"name": "plan.delete", "path": "/mcp/tool/plan.delete", "method": "POST"}]}
event: ping
data: {"ts": 1762248336}
event: ping
data: {"ts": 1762248338}
