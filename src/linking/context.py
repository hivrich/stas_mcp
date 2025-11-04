"""Helpers for accessing linked connection context."""

from __future__ import annotations

from dataclasses import dataclass

from src.linking import get_status


class LinkingRequired(RuntimeError):
    """Raised when an operation requires a linked user."""


@dataclass(frozen=True)
class LinkedUser:
    connection_id: str
    user_id: int


def get_linked_user_id(connection_id: str) -> int:
    """Return the linked user id for the given connection.

    Raises
    ------
    LinkingRequired
        When the connection does not have an associated user id.
    """

    status = get_status(connection_id)
    if not isinstance(status, dict) or not status.get("linked"):
        raise LinkingRequired("connection must be linked")

    user_id = status.get("user_id")
    if not isinstance(user_id, int):
        raise LinkingRequired("linked connection is missing user id")

    return user_id


__all__ = ["LinkedUser", "LinkingRequired", "get_linked_user_id"]
