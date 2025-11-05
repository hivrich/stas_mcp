"""Helpers for normalizing plan external identifiers."""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Mapping, Tuple


def _find_min_day(days: Iterable[Mapping[str, Any]] | None) -> str | None:
    """Return minimal ISO date (YYYY-MM-DD) from days collection if available."""
    if not days:
        return None
    min_day: dt.date | None = None
    for day in days:
        if not isinstance(day, Mapping):
            continue
        value = day.get("date")
        if not isinstance(value, str):
            continue
        date_str = value.strip()
        if not date_str:
            continue
        try:
            day_value = dt.date.fromisoformat(date_str[:10])
        except ValueError:
            continue
        if min_day is None or day_value < min_day:
            min_day = day_value
    if min_day is None:
        return None
    return min_day.isoformat()


def normalize_plan_external_id(
    raw_external_id: str,
    *,
    days: Iterable[Mapping[str, Any]] | None = None,
) -> Tuple[str, str]:
    """Return ``(raw, normalized)`` tuple for plan external identifiers.

    ``raw`` preserves the caller-provided value (falling back to ``"plan:auto"``)
    while ``normalized`` ensures the ``plan:`` prefix and injects the minimal
    date from ``days`` when available as ``plan:YYYY-MM-DD:<slug>``.
    """

    raw_value = (raw_external_id or "").strip()
    if not raw_value:
        raw_value = "plan:auto"

    min_day = _find_min_day(days if isinstance(days, Iterable) else None)

    if raw_value.startswith("plan:"):
        slug = raw_value[5:]
    else:
        slug = raw_value

    slug = slug.lstrip(":")
    if not slug:
        slug = "auto"

    if min_day:
        remainder = slug.split(":", 1)[1] if ":" in slug else slug
        if not remainder:
            remainder = "auto"
        normalized = f"plan:{min_day}:{remainder}"
    else:
        normalized = raw_value if raw_value.startswith("plan:") else f"plan:{slug}"

    return raw_value, normalized


__all__ = ["normalize_plan_external_id"]
