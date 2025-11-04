from __future__ import annotations

from typing import Any, Optional


class LifespanManager:
    """Lightweight replacement for asgi_lifespan.LifespanManager."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self._context: Optional[Any] = None

    async def __aenter__(self) -> "LifespanManager":
        router = getattr(self.app, "router", None)
        if router is None:
            return self
        lifespan_context = getattr(router, "lifespan_context", None)
        if lifespan_context is None:
            return self
        self._context = lifespan_context(self.app)
        await self._context.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._context is None:
            return None
        await self._context.__aexit__(exc_type, exc, tb)
        self._context = None
        return None
