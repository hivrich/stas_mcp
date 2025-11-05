# src/mcp/tools_plan.py
from typing import Any, Dict, Tuple

def _ok(data: Dict[str, Any], label: str) -> Tuple[Dict[str, Any], str]:
    return {"ok": True, **data}, f"{label}: ok"

def _err(code: str, msg: str, label: str) -> Tuple[Dict[str, Any], str]:
    return {"ok": False, "error": {"code": code, "message": msg}}, f"{label}: error"

# ---- READ/LIST/STATUS (безопасные) ----
async def plan_list(arguments: Dict[str, Any]):
    window = {"oldest": arguments.get("oldest"),
              "newest": arguments.get("newest"),
              "category": arguments.get("category")}
    limit = arguments.get("limit")
    return _ok({"items": [], "window": window, "limit": limit}, "plan_list")

async def plan_status(arguments: Dict[str, Any]):
    return _ok({"status": "idle"}, "plan_status")

async def plan_validate(arguments: Dict[str, Any]):
    return _ok({"valid": True, "issues": []}, "plan_validate")

# ---- WRITE (опасные) — требуют confirm:true ----

def _require_confirm(args: Dict[str, Any], label: str):
    if args.get("confirm") is not True:
        raise ValueError(f"{label} requires confirm:true")

async def plan_update(arguments: Dict[str, Any]):
    _require_confirm(arguments, "plan_update")
    patch = arguments.get("patch")
    if not isinstance(patch, dict):
        return _err("bad_request", "patch must be an object", "plan_update")
    return _ok({"updated": True, "patch": patch}, "plan_update")

async def plan_publish(arguments: Dict[str, Any]):
    _require_confirm(arguments, "plan_publish")
    note = arguments.get("note")
    return _ok({"published": True, "note": note}, "plan_publish")

async def plan_delete(arguments: Dict[str, Any]):
    _require_confirm(arguments, "plan_delete")
    _id = arguments.get("id")
    if not _id:
        return _err("bad_request", "id is required", "plan_delete")
    return _ok({"deleted": True, "id": _id}, "plan_delete")
