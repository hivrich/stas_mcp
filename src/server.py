import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional

from fastapi import FastAPI, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
SCHEMA_PATH = ASSETS_DIR / "schema.plan.json"
AUDIT_LOG_PATH = DATA_DIR / "audit.log"

PING_INTERVAL_SECONDS = 15


def _load_schema() -> Dict[str, Any]:
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    return json.loads(text)


PLAN_SCHEMA = _load_schema()


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(self, entry: Dict[str, Any]) -> None:
        payload = dict(entry)
        payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        line = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class PlanStore:
    def __init__(self) -> None:
        self._responses: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, external_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            stored = self._responses.get(external_id)
            if stored is None:
                return None
            return dict(stored)

    def upsert(self, external_id: str, response: Dict[str, Any]) -> None:
        with self._lock:
            self._responses[external_id] = dict(response)

    def remove(self, external_id: str) -> None:
        with self._lock:
            self._responses.pop(external_id, None)


app = FastAPI()
audit_logger = AuditLogger(AUDIT_LOG_PATH)
plan_store = PlanStore()


MANIFEST = {
    "resources": [
        {"name": "current.json", "path": "/mcp/resource/current.json"},
        {"name": "last_training.json", "path": "/mcp/resource/last_training.json"},
        {"name": "schema.plan.json", "path": "/mcp/resource/schema.plan.json"},
    ],
    "tools": [
        {"name": "plan.validate", "path": "/mcp/tool/plan.validate", "method": "POST"},
        {"name": "plan.publish", "path": "/mcp/tool/plan.publish", "method": "POST"},
        {"name": "plan.delete", "path": "/mcp/tool/plan.delete", "method": "POST"},
    ],
}


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {"ok": True, "ts": int(time.time())}


@app.get("/mcp/resource/current.json")
def current_resource() -> Dict[str, Any]:
    return {"ok": True, "athlete_id": "i-demo", "week": "2025-W45", "risks": []}


@app.get("/mcp/resource/last_training.json")
def last_training_resource() -> Dict[str, Any]:
    return {"ok": True, "last": {"date": "2025-11-02", "type": "Run", "km": 10}}


@app.get("/mcp/resource/schema.plan.json")
def schema_resource() -> Dict[str, Any]:
    return PLAN_SCHEMA


@app.post("/mcp/tool/plan.validate")
def plan_validate(request: Request) -> Dict[str, Any]:
    payload = request.json()
    draft = _extract_draft(payload)
    errors = _validate_draft(draft)
    ok = not errors
    response = {"ok": ok, "errors": errors, "warnings": [], "diff": {}}
    audit_logger.log({"tool": "plan.validate", "ok": ok, "errors": errors, "payload": payload})
    return response


@app.post("/mcp/tool/plan.publish")
def plan_publish(request: Request) -> Dict[str, Any]:
    payload = request.json()
    confirm = isinstance(payload, dict) and payload.get("confirm") is True
    if not confirm:
        response = {"ok": False, "need_confirm": True, "hint": "Add confirm:true"}
        audit_logger.log({"tool": "plan.publish", "ok": False, "reason": "missing_confirm", "payload": payload})
        return response

    draft = _extract_draft(payload)
    errors = _validate_draft(draft)
    if errors:
        audit_logger.log({"tool": "plan.publish", "ok": False, "errors": errors, "payload": payload})
        raise HTTPException(status_code=400, detail={"ok": False, "errors": errors})

    external_id = _resolve_external_id(payload, draft)
    if external_id is None:
        audit_logger.log({"tool": "plan.publish", "ok": False, "reason": "missing_external_id", "payload": payload})
        raise HTTPException(status_code=400, detail={"ok": False, "errors": ["external_id is required"]})

    existing = plan_store.get(external_id)
    if existing:
        audit_logger.log({"tool": "plan.publish", "ok": True, "external_id": external_id, "idempotent": True})
        return existing

    days = draft.get("days") if isinstance(draft, dict) else []
    days_written = len(days) if isinstance(days, list) else 0
    response = {
        "ok": True,
        "days_written": days_written,
        "external_id": external_id,
        "at": datetime.now(timezone.utc).isoformat(),
        "source": "mcp-stub",
    }
    plan_store.upsert(external_id, response)
    audit_logger.log({"tool": "plan.publish", "ok": True, "external_id": external_id, "days_written": days_written})
    return response


@app.post("/mcp/tool/plan.delete")
def plan_delete(request: Request) -> Dict[str, Any]:
    payload = request.json()
    if not isinstance(payload, dict):
        audit_logger.log({"tool": "plan.delete", "ok": False, "reason": "invalid_payload", "payload": payload})
        raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_payload"})

    confirm = payload.get("confirm") is True
    if not confirm:
        response = {"ok": False, "need_confirm": True, "hint": "Add confirm:true"}
        audit_logger.log({"tool": "plan.delete", "ok": False, "reason": "missing_confirm", "payload": payload})
        return response

    external_id = payload.get("external_id")
    if not isinstance(external_id, str):
        audit_logger.log({"tool": "plan.delete", "ok": False, "reason": "missing_external_id", "payload": payload})
        raise HTTPException(status_code=400, detail={"ok": False, "error": "missing_external_id"})

    plan_store.remove(external_id)
    response = {"ok": True, "external_id": external_id}
    audit_logger.log({"tool": "plan.delete", "ok": True, "external_id": external_id})
    return response


@app.get("/sse")
def sse_endpoint() -> EventSourceResponse:
    return EventSourceResponse(_sse_event_generator)


def _extract_draft(payload: Any) -> Any:
    if isinstance(payload, dict) and "draft" in payload:
        return payload["draft"]
    return payload


def _resolve_external_id(payload: Any, draft: Any) -> Optional[str]:
    if isinstance(payload, dict) and isinstance(payload.get("external_id"), str):
        return payload["external_id"]
    if isinstance(draft, dict) and isinstance(draft.get("external_id"), str):
        return draft["external_id"]
    return None


def _validate_draft(draft: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(draft, dict):
        return ["draft must be an object"]

    for field in ("external_id", "athlete_id", "days"):
        if field not in draft:
            errors.append(f"missing required field: {field}")

    if "external_id" in draft and not isinstance(draft["external_id"], str):
        errors.append("external_id must be a string")
    if "athlete_id" in draft and not isinstance(draft["athlete_id"], str):
        errors.append("athlete_id must be a string")
    if "meta" in draft and not isinstance(draft["meta"], dict):
        errors.append("meta must be an object")

    days = draft.get("days")
    if not isinstance(days, list):
        errors.append("days must be an array")
    else:
        for idx, day in enumerate(days):
            if not isinstance(day, dict):
                errors.append(f"days[{idx}] must be an object")
                continue
            for key in ("date", "title", "blocks"):
                if key not in day:
                    errors.append(f"days[{idx}] missing required field: {key}")
            if "date" in day and not isinstance(day["date"], str):
                errors.append(f"days[{idx}].date must be a string")
            if "title" in day and not isinstance(day["title"], str):
                errors.append(f"days[{idx}].title must be a string")
            if "blocks" in day and not isinstance(day["blocks"], list):
                errors.append(f"days[{idx}].blocks must be an array")
    return errors


def _sse_event_generator() -> Iterable[Dict[str, Any]]:
    yield {"event": "manifest", "data": json.dumps(MANIFEST)}
    while True:
        time.sleep(PING_INTERVAL_SECONDS)
        yield {"event": "ping", "data": str(int(time.time()))}


def main() -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
