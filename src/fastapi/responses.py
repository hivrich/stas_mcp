import json
from typing import Any, Dict


class ORJSONResponse:
    def __init__(
        self,
        content: Any,
        status_code: int = 200,
        headers: Dict[str, str] | None = None,
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def render(self) -> bytes:
        return json.dumps(self.content).encode("utf-8")
