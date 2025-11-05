"""In-memory session storage for the MCP bridge.

This module keeps the current session's ``user_id`` in process memory. It is
intended for the single-process Render deployment used in development and does
not provide any cross-process or cross-tenant isolation.
"""

from __future__ import annotations

from typing import Optional

_USER_ID: Optional[int] = None


def set_user_id(user_id: int) -> None:
    """Persist ``user_id`` for the current worker process."""
    if isinstance(user_id, bool):  # guard against bool being ``int`` subclass
        raise ValueError("user_id must be an integer")
    try:
        coerced = int(user_id)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError("user_id must be an integer") from exc
    if coerced < 0:
        raise ValueError("user_id must be non-negative")

    global _USER_ID
    _USER_ID = coerced


def get_user_id() -> Optional[int]:
    """Return the stored ``user_id`` if present."""

    return _USER_ID


def clear_user_id() -> None:
    """Remove the stored ``user_id`` value."""

    global _USER_ID
    _USER_ID = None
