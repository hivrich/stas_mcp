# UAT Evidence (PROD)

TARGET_BASE=https://stas-mcp.onrender.com

## Steps

### A) Health & SSE
- `GET $TARGET_BASE/healthz` → `{"ok":true}`
- `GET $TARGET_BASE/sse` → first event `manifest`

### B) Connect flow
- From ChatGPT MCP connector, open `$TARGET_BASE/link` when prompted and submit your real `user_id` once (per connection).

### C) Read
- `GET $TARGET_BASE/mcp/resource/current.json` → JSON summary from gateway
- `GET $TARGET_BASE/mcp/resource/last_training.json` → most recent finished training

### D) Validate/Publish
- `POST $TARGET_BASE/mcp/tool/plan.validate` with a small draft (1 day)
- `POST $TARGET_BASE/mcp/tool/plan.publish` with `{external_id:"plan:2025-w45", draft:{…}, confirm:true}` → `{ok:true, days_written:>=1}`

### E) Idempotency
- Repeat publish with the same `external_id` → gateway handles dedupe (no duplicate events)

### F) Delete
- `POST $TARGET_BASE/mcp/tool/plan.delete {external_id:"plan:2025-w45"}` → `{need_confirm:true}`
- `POST $TARGET_BASE/mcp/tool/plan.delete {external_id:"plan:2025-w45", confirm:true}` → `{ok:true}`
