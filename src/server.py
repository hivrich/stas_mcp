import argparse
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RESOURCE_DIR = DATA_DIR / "resources"
SCHEMA_PATH = Path(__file__).resolve().parent / "assets" / "schema.plan.json"
AUDIT_LOG_PATH = DATA_DIR / "audit.log"
LINKS_PATH = DATA_DIR / "links.json"
PLANS_PATH = DATA_DIR / "plans.json"

MANIFEST = {
    "server": "stas-mcp-bridge-stub",
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

PING_INTERVAL = 15


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, entry: Dict[str, Any]) -> None:
        with self.lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


class LinkStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self._links: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, dict):
                        self._links = {str(k): str(v) for k, v in data.items()}
            except Exception:
                self._links = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(self._links, fh, ensure_ascii=False, indent=2)

    def get(self, connection_id: str) -> Optional[str]:
        with self.lock:
            return self._links.get(connection_id)

    def set(self, connection_id: str, user_id: str) -> None:
        with self.lock:
            self._links[connection_id] = user_id
            self._save()


class PlanStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self._plans: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, dict):
                        self._plans = data
            except Exception:
                self._plans = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(self._plans, fh, ensure_ascii=False, indent=2)

    def get(self, external_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self._plans.get(external_id)

    def set(self, external_id: str, payload: Dict[str, Any]) -> None:
        with self.lock:
            self._plans[external_id] = payload
            self._save()

    def delete(self, external_id: str) -> None:
        with self.lock:
            if external_id in self._plans:
                del self._plans[external_id]
                self._save()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_schema() -> Dict[str, Any]:
    with SCHEMA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


SCHEMA = load_schema()

audit_logger = AuditLogger(AUDIT_LOG_PATH)
link_store = LinkStore(LINKS_PATH)
plan_store = PlanStore(PLANS_PATH)


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "stas-mcp/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # Reduce noise; standard output not required.
        pass

    # Utility helpers
    def _json_response(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _require_connection(self) -> Tuple[Optional[str], Optional[str]]:
        connection_id = self.headers.get("x-connection-id")
        if not connection_id:
            return None, None
        user_id = link_store.get(connection_id)
        return connection_id, user_id

    def _ensure_account_link(self, connection_id: Optional[str], user_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not connection_id:
            return {
                "ok": False,
                "error": "missing_connection_id",
                "hint": "Provide X-Connection-Id header",
            }
        if not user_id:
            return {
                "ok": False,
                "need_link": True,
                "link_hint": "Open /_/link to enter your internal UserID",
            }
        return None

    # Request handlers
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._json_response(200, {"ok": True, "ts": int(time.time())})
            return
        if parsed.path == "/sse":
            self.handle_sse()
            return
        if parsed.path.startswith("/mcp/resource/"):
            resource_name = parsed.path.replace("/mcp/resource/", "", 1)
            self.handle_resource(resource_name)
            return
        if parsed.path == "/_/link":
            self.handle_link(parsed)
            return
        self.send_error(404, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/mcp/tool/"):
            tool_name = parsed.path.replace("/mcp/tool/", "", 1)
            self.handle_tool(tool_name)
            return
        if parsed.path == "/_/link":
            self.handle_link(parsed, method="POST")
            return
        self.send_error(404, "Not Found")

    def handle_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            manifest_payload = json.dumps(MANIFEST, ensure_ascii=False)
            self.wfile.write(f"event: manifest\ndata: {manifest_payload}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                timestamp = str(int(time.time()))
                self.wfile.write(f"event: ping\ndata: {timestamp}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(PING_INTERVAL)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def handle_resource(self, name: str) -> None:
        if name == "schema.plan.json":
            path = SCHEMA_PATH
        else:
            path = RESOURCE_DIR / name
        if not path.exists():
            self.send_error(404, "Not Found")
            return
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(500, "Failed to load resource")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def handle_link(self, parsed, method: str = "GET") -> None:
        if method == "POST":
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b""
            params = parse_qs(body.decode("utf-8"))
        else:
            params = parse_qs(parsed.query)
        connection_id = (params.get("connection_id") or [""])[0]
        user_id = (params.get("user_id") or [""])[0]
        message = ""
        if connection_id and user_id:
            link_store.set(connection_id, user_id)
            message = f"Linked connection {connection_id} to user {user_id}."
        elif method == "POST":
            message = "Both connection_id and user_id are required."
        html = f"""
<!DOCTYPE html>
<html lang=\"en\">
<head><meta charset=\"utf-8\"><title>Link Account</title></head>
<body>
<h1>Link Connection</h1>
<p>{message}</p>
<form method=\"post\">
  <label>Connection ID <input type=\"text\" name=\"connection_id\" value=\"{connection_id}\" required></label><br>
  <label>User ID <input type=\"text\" name=\"user_id\" value=\"{user_id}\" required></label><br>
  <button type=\"submit\">Link</button>
</form>
</body>
</html>
"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_tool(self, name: str) -> None:
        raw_body = self._read_body()
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}") if raw_body else {}
        except json.JSONDecodeError:
            self._json_response(400, {"ok": False, "error": "invalid_json"})
            return
        connection_id, user_id = self._require_connection()

        if name == "plan.validate":
            draft = self._extract_draft(payload)
            ok, errors = validate_plan_schema(draft)
            status = "ok" if ok else "error"
            audit_logger.log(
                {
                    "timestamp": utc_now_iso(),
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "op": "plan.validate",
                    "external_id": draft.get("external_id") if isinstance(draft, dict) else None,
                    "status": status,
                }
            )
            self._json_response(200, {"ok": ok, "errors": errors, "warnings": [], "diff": {}})
            return

        # plan.publish and plan.delete require account link
        link_error = self._ensure_account_link(connection_id, user_id)
        if link_error:
            self._json_response(403, link_error)
            return

        if name == "plan.publish":
            draft = self._extract_draft(payload)
            ok, errors = validate_plan_schema(draft)
            if not ok:
                audit_logger.log(
                    {
                        "timestamp": utc_now_iso(),
                        "connection_id": connection_id,
                        "user_id": user_id,
                        "op": "plan.publish",
                        "external_id": draft.get("external_id") if isinstance(draft, dict) else None,
                        "status": "error",
                    }
                )
                self._json_response(400, {"ok": False, "errors": errors, "warnings": [], "diff": {}})
                return
            confirm = payload.get("confirm") if isinstance(payload, dict) else None
            if confirm is not True:
                self._json_response(200, {"ok": False, "need_confirm": True, "hint": "Add confirm:true"})
                return
            external_id = self._resolve_external_id(payload, draft)
            plan_store.set(external_id, draft)
            response = {
                "ok": True,
                "days_written": len(draft.get("days", []) if isinstance(draft, dict) else []),
                "external_id": external_id,
                "at": utc_now_iso(),
                "source": "mcp-stub",
            }
            audit_logger.log(
                {
                    "timestamp": response["at"],
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "op": "plan.publish",
                    "external_id": external_id,
                    "status": "ok",
                }
            )
            self._json_response(200, response)
            return

        if name == "plan.delete":
            if not isinstance(payload, dict):
                self._json_response(400, {"ok": False, "error": "invalid_payload"})
                return
            external_id = payload.get("external_id")
            if not external_id:
                self._json_response(400, {"ok": False, "error": "missing_external_id"})
                return
            confirm = payload.get("confirm")
            if confirm is not True:
                self._json_response(200, {"ok": False, "need_confirm": True, "hint": "Add confirm:true"})
                return
            plan_store.delete(external_id)
            audit_logger.log(
                {
                    "timestamp": utc_now_iso(),
                    "connection_id": connection_id,
                    "user_id": user_id,
                    "op": "plan.delete",
                    "external_id": external_id,
                    "status": "ok",
                }
            )
            self._json_response(200, {"ok": True, "external_id": external_id})
            return

        self._json_response(404, {"ok": False, "error": "unknown_tool"})

    def _extract_draft(self, payload: Any) -> Any:
        if isinstance(payload, dict) and "draft" in payload:
            return payload["draft"]
        return payload

    def _resolve_external_id(self, payload: Any, draft: Any) -> str:
        if isinstance(payload, dict) and isinstance(payload.get("external_id"), str):
            return payload["external_id"]
        if isinstance(draft, dict) and isinstance(draft.get("external_id"), str):
            return draft["external_id"]
        return "plan:demo"


def validate_plan_schema(data: Any) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(data, dict):
        errors.append("draft must be an object")
        return False, errors
    required_top = ["external_id", "athlete_id", "days"]
    for key in required_top:
        if key not in data:
            errors.append(f"missing required field: {key}")
    if "external_id" in data and not isinstance(data["external_id"], str):
        errors.append("external_id must be a string")
    if "athlete_id" in data and not isinstance(data["athlete_id"], str):
        errors.append("athlete_id must be a string")
    if "meta" in data and not isinstance(data["meta"], dict):
        errors.append("meta must be an object")
    days = data.get("days")
    if not isinstance(days, list):
        errors.append("days must be an array")
    else:
        for idx, item in enumerate(days):
            if not isinstance(item, dict):
                errors.append(f"days[{idx}] must be an object")
                continue
            for req_key in ("date", "title", "blocks"):
                if req_key not in item:
                    errors.append(f"days[{idx}] missing required field: {req_key}")
            if "date" in item and not isinstance(item["date"], str):
                errors.append(f"days[{idx}].date must be a string")
            if "title" in item and not isinstance(item["title"], str):
                errors.append(f"days[{idx}].title must be a string")
            if "blocks" in item and not isinstance(item["blocks"], list):
                errors.append(f"days[{idx}].blocks must be an array")
    is_valid = len(errors) == 0
    return is_valid, errors


def run_server(host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), MCPHandler)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MCP bridge server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    port = args.port
    server: Optional[ThreadingHTTPServer] = None
    for candidate in (port, port + 1, port + 2):
        try:
            server = run_server(args.host, candidate)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("Unable to bind to a port")

    print(f"Serving on http://{args.host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
