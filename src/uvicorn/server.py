import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import ORJSONResponse
from sse_starlette.sse import EventSourceResponse


class _AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, RequestHandlerClass, app):
        super().__init__(server_address, RequestHandlerClass)
        self.app = app


class _RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        body = b""
        if method == "POST":
            length = int(self.headers.get("content-length", 0))
            if length:
                body = self.rfile.read(length)
        headers = {k.lower(): v for k, v in self.headers.items()}
        request = Request(body, headers)
        try:
            result = self.server.app.dispatch(method, path, request)
        except HTTPException as exc:
            payload = json.dumps(exc.detail).encode("utf-8")
            self.send_response(exc.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if isinstance(result, EventSourceResponse):
            self._stream_sse(result)
            return

        status_code = 200
        headers_out = {}
        if isinstance(result, ORJSONResponse):
            payload = result.render()
            status_code = result.status_code
            headers_out = result.headers
        else:
            payload = json.dumps(result).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in headers_out.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _stream_sse(self, response: EventSourceResponse) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            for event in response.stream():
                if event is None:
                    continue
                event_name = event.get("event")
                data = event.get("data", "")
                if event_name:
                    self.wfile.write(f"event: {event_name}\n".encode("utf-8"))
                lines = str(data).splitlines() or [""]
                for line in lines:
                    self.wfile.write(f"data: {line}\n".encode("utf-8"))
                self.wfile.write(b"\n")
                self.wfile.flush()
        except BrokenPipeError:
            pass


def serve(app: Any, host: str, port: int) -> None:
    server = _AppServer((host, port), _RequestHandler, app)
    print(f"Serving on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
