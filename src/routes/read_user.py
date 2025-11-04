"""Public read-only endpoints for user data."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, Iterable, Mapping, MutableMapping

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from src.clients import gw


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user", tags=["user-read"])


def _today() -> date:
    return date.today()


def _filter_future_trainings(
    items: Iterable[Mapping[str, Any]], *, today: date
) -> list[Dict[str, Any]]:
    filtered: list[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, MutableMapping):
            continue
        training_date = _extract_training_date(item)
        if training_date is not None and training_date > today:
            continue
        filtered.append(dict(item))
    return filtered


def _extract_training_date(item: Mapping[str, Any]) -> date | None:
    raw_date = item.get("date")
    if isinstance(raw_date, str):
        try:
            return date.fromisoformat(raw_date)
        except ValueError:
            return None
    return None


@router.get("/summary")
async def read_user_summary(user_id: int = Query(...)) -> Any:
    logger.info("read.user.summary request user_id=%s", user_id)
    try:
        payload = await gw.get_user_summary(user_id)
    except gw.GwUnavailable:
        status_code = 503
        logger.info("read.user.summary status=%s user_id=%s", status_code, user_id)
        return JSONResponse({"error": "GwUnavailable"}, status_code=status_code)
    except gw.GwBadResponse as exc:
        status_code = 502
        body: Dict[str, Any] = {"error": "GwBadResponse"}
        if exc.status_code is not None:
            body["status"] = exc.status_code
        logger.info("read.user.summary status=%s user_id=%s", status_code, user_id)
        return JSONResponse(body, status_code=status_code)

    logger.info("read.user.summary status=200 user_id=%s", user_id)
    return payload


@router.get("/last_training")
async def read_user_last_training(
    user_id: int = Query(...),
    oldest: date | None = Query(default=None),
    newest: date | None = Query(default=None),
) -> Dict[str, Any]:
    logger.info("read.user.last_training request user_id=%s", user_id)

    today = _today()
    if newest is None:
        newest = today
    if oldest is None:
        oldest = newest - timedelta(days=14)
    if oldest > newest:
        logger.info("read.user.last_training status=422 user_id=%s", user_id)
        raise HTTPException(status_code=422, detail="oldest must be before newest")

    try:
        trainings = await gw.get_trainings(user_id, oldest=oldest, newest=newest)
    except gw.GwUnavailable:
        status_code = 503
        logger.info(
            "read.user.last_training status=%s user_id=%s", status_code, user_id
        )
        return JSONResponse({"error": "GwUnavailable"}, status_code=status_code)
    except gw.GwBadResponse as exc:
        status_code = 502
        body: Dict[str, Any] = {"error": "GwBadResponse"}
        if exc.status_code is not None:
            body["status"] = exc.status_code
        logger.info(
            "read.user.last_training status=%s user_id=%s", status_code, user_id
        )
        return JSONResponse(body, status_code=status_code)

    filtered = _filter_future_trainings(trainings, today=min(today, newest))
    logger.info("read.user.last_training status=200 user_id=%s", user_id)
    return {"items": filtered}
