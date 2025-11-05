# src/mcp/tools_plan.py
# Заглушки для планов: всегда ok:true и пустые/минимальные структуры.

from typing import Any, Dict, Tuple

def _ok(data: Dict[str, Any], label: str) -> Tuple[Dict[str, Any], str]:
    return {"ok": True, **data}, f"{label}: ok"

async def plan_list(arguments: Dict[str, Any]):
    window = {"oldest": arguments.get("oldest"), "newest": arguments.get("newest"), "category": arguments.get("category")}
    return _ok({"items": [], "window": window}, "plan.list")

async def plan_status(arguments: Dict[str, Any]):
    return _ok({"status": "idle"}, "plan.status")

async def plan_update(arguments: Dict[str, Any]):
    return _ok({"updated": True, "patch": arguments.get("patch")}, "plan.update")

async def plan_publish(arguments: Dict[str, Any]):
    return _ok({"published": True, "note": arguments.get("note")}, "plan.publish")

async def plan_delete(arguments: Dict[str, Any]):
    return _ok({"deleted": True, "id": arguments.get("id")}, "plan.delete")

async def plan_validate(arguments: Dict[str, Any]):
    return _ok({"valid": True, "issues": []}, "plan.validate")
