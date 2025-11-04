"""In-memory linking state store."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from threading import Lock
from typing import Dict, Optional


@dataclass
class _LinkState:
    linked: bool = False
    user_id: Optional[int] = None

    def to_payload(self) -> Dict[str, object]:
        data = asdict(self)
        if data["user_id"] is None:
            data.pop("user_id")
        return data


class _InMemoryLinkStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._states: Dict[str, _LinkState] = {}

    def set_pending(self, connection_id: str) -> None:
        if not connection_id:
            raise ValueError("connection_id must be provided")
        with self._lock:
            state = self._states.get(connection_id)
            if state and state.linked:
                return
            self._states[connection_id] = _LinkState(linked=False, user_id=None)

    def set_linked(self, connection_id: str, user_id: int) -> None:
        if not connection_id:
            raise ValueError("connection_id must be provided")
        if not isinstance(user_id, int):
            raise TypeError("user_id must be an int")
        with self._lock:
            self._states[connection_id] = _LinkState(linked=True, user_id=user_id)

    def get_status(self, connection_id: str) -> Dict[str, object]:
        if not connection_id:
            raise ValueError("connection_id must be provided")
        with self._lock:
            state = self._states.get(connection_id)
            if not state:
                return {"linked": False}
            return state.to_payload()

    def reset(self) -> None:
        with self._lock:
            self._states.clear()


_store = _InMemoryLinkStore()


def set_pending(connection_id: str) -> None:
    _store.set_pending(connection_id)


def set_linked(connection_id: str, user_id: int) -> None:
    _store.set_linked(connection_id, user_id)


def get_status(connection_id: str) -> Dict[str, object]:
    return _store.get_status(connection_id)


def reset() -> None:
    """Reset store state (intended for tests)."""
    _store.reset()


__all__ = ["set_pending", "set_linked", "get_status", "reset"]
