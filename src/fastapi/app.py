import inspect
import json
from typing import Any, Callable, Dict, Tuple


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Request:
    def __init__(self, body: bytes, headers: Dict[str, str]) -> None:
        self._body = body
        self.headers = headers

    def json(self) -> Any:
        if not self._body:
            return None
        try:
            return json.loads(self._body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400, detail={"ok": False, "error": "invalid_json"}
            ) from exc


class FastAPI:
    def __init__(self, default_response_class: Any = None) -> None:
        self._routes: Dict[Tuple[str, str], Callable[..., Any]] = {}
        self.default_response_class = default_response_class

    def get(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._register("GET", path)

    def post(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._register("POST", path)

    def _register(
        self, method: str, path: str
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._routes[(method.upper(), path)] = func
            return func

        return decorator

    def dispatch(self, method: str, path: str, request: Request) -> Any:
        handler = self._routes.get((method.upper(), path))
        if handler is None:
            raise HTTPException(status_code=404, detail={"detail": "Not Found"})
        sig = inspect.signature(handler)
        if len(sig.parameters) == 0:
            return handler()
        return handler(request)


def Body(default: Any = None) -> Any:
    return default
