from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

from fastapi import FastAPI, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
SCHEMA_PATH = ASSETS_DIR / "schema.plan.json"

with SCHEMA_PATH.open("r", encoding="utf-8") as handle:
    PLAN_SCHEMA: Dict[str, Any] = json.load(handle)

app = FastAPI()


_CURRENT_RESOURCE = {
    "external_id": "plan:current",
    "athlete_id": "i-demo",
    "week": "2025-W45",
    "status": "ok",
}

_LAST_TRAINING_RESOURCE = {
    "athlete_id": "i-demo",
    "last": {"date": "2025-11-02", "type": "Run", "distance_km": 10},
}

_MANIFEST = {
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


_published_plans: Dict[str, Dict[str, Any]] = {}


def _extract_plan(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("draft"), dict):
        return payload["draft"]
    if isinstance(payload, dict):
        return {k: v for k, v in payload.items() if k != "confirm"}
    raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_payload"})


def _validate(plan: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    if not isinstance(plan, dict):
        errors.append({"path": [], "message": "plan must be an object"})
        return {"ok": False, "errors": errors, "warnings": [], "diff": {}}

    for field in ("external_id", "athlete_id", "days"):
        if field not in plan:
            errors.append({"path": [field], "message": "Missing required property"})

    if "external_id" in plan and not isinstance(plan["external_id"], str):
        errors.append({"path": ["external_id"], "message": "Must be a string"})
    if "athlete_id" in plan and not isinstance(plan["athlete_id"], str):
        errors.append({"path": ["athlete_id"], "message": "Must be a string"})
    if "meta" in plan and not isinstance(plan["meta"], dict):
        errors.append({"path": ["meta"], "message": "Must be an object"})

    days = plan.get("days")
    if not isinstance(days, list):
        errors.append({"path": ["days"], "message": "Must be an array"})
    else:
        for index, day in enumerate(days):
            if not isinstance(day, dict):
                errors.append({"path": ["days", index], "message": "Must be an object"})
                continue
            for key in ("date", "title", "blocks"):
                if key not in day:
                    errors.append({"path": ["days", index, key], "message": "Missing required property"})
            if "date" in day and not isinstance(day["date"], str):
                errors.append({"path": ["days", index, "date"], "message": "Must be a string"})
            if "title" in day and not isinstance(day["title"], str):
                errors.append({"path": ["days", index, "title"], "message": "Must be a string"})
            if "blocks" in day and not isinstance(day["blocks"], list):
                errors.append({"path": ["days", index, "blocks"], "message": "Must be an array"})

    return {"ok": not errors, "errors": errors, "warnings": [], "diff": {}}


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {"ok": True, "ts": int(time.time())}


@app.get("/mcp/resource/current.json")
def current_resource() -> Dict[str, Any]:
    return _CURRENT_RESOURCE


@app.get("/mcp/resource/last_training.json")
def last_training_resource() -> Dict[str, Any]:
    return _LAST_TRAINING_RESOURCE


@app.get("/mcp/resource/schema.plan.json")
def schema_resource() -> Dict[str, Any]:
    return PLAN_SCHEMA


@app.post("/mcp/tool/plan.validate")
def plan_validate(request: Request) -> Dict[str, Any]:
    payload = request.json()
    plan = _extract_plan(payload)
    return _validate(plan)


@app.post("/mcp/tool/plan.publish")
def plan_publish(request: Request) -> Dict[str, Any]:
    payload = request.json()
    confirm = isinstance(payload, dict) and payload.get("confirm") is True
    if not confirm:
        return {"ok": False, "need_confirm": True, "hint": "Add confirm:true"}

    plan = _extract_plan(payload)
    validation = _validate(plan)
    if not validation["ok"]:
        raise HTTPException(status_code=400, detail=validation)

    external_id = plan.get("external_id")
    if not isinstance(external_id, str):
        raise HTTPException(
            status_code=400,
            detail={"ok": False, "errors": ["external_id is required"]},
        )

    stored = _published_plans.get(external_id)
    if stored is not None:
        return stored

    result = {
        "ok": True,
        "external_id": external_id,
        "status": "published",
        "days_written": len(plan.get("days", [])) if isinstance(plan.get("days"), list) else 0,
    }
    _published_plans[external_id] = result
    return result


@app.post("/mcp/tool/plan.delete")
def plan_delete(request: Request) -> Dict[str, Any]:
    payload = request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_payload"})

    if payload.get("confirm") is not True:
        return {"ok": False, "need_confirm": True, "hint": "Add confirm:true"}

    external_id = payload.get("external_id")
    if not isinstance(external_id, str):
        raise HTTPException(status_code=400, detail={"ok": False, "error": "missing_external_id"})

    _published_plans.pop(external_id, None)
    return {"ok": True, "external_id": external_id}


def _sse_stream() -> Iterable[Dict[str, Any]]:
    yield {"event": "manifest", "data": json.dumps(_MANIFEST)}
    while True:
        time.sleep(2)
        yield {"event": "ping", "data": json.dumps({"ts": int(time.time())})}


@app.get("/sse")
def sse_endpoint() -> EventSourceResponse:
    return EventSourceResponse(_sse_stream())


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
