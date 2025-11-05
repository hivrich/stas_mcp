# src/mcp/tools_read.py
# Чтение данных (summary/trainings). Внешние вызовы обёрнуты и никогда не пробрасывают исключения наружу.

from __future__ import annotations
import os
import json
from typing import Any, Dict, Tuple, List, Optional
from datetime import datetime, timedelta

import aiohttp

# ------------------------ utils ------------------------

def _window_14d() -> Tuple[str, str]:
    newest = datetime.utcnow().date()
    oldest = newest - timedelta(days=14)
    return oldest.isoformat(), newest.isoformat()

def _get(s: Dict[str, Any], key: str, default=None):
    v = s.get(key)
    return v if v not in (None, "") else default

def _auth_bearer(user_id: Optional[int]) -> Optional[str]:
    """
    Минимальная совместимость с вашим шлюзом:
    допускаем BEARER вида t_<base64url({"uid":user_id})> если задан user_id.
    Также допускаем внешний токен через ENV STAS_GW_TOKEN.
    """
    env_token = os.getenv("STAS_GW_TOKEN")
    if env_token:
        return env_token
    if not user_id:
        return None
    try:
        payload = json.dumps({"uid": int(user_id)}).encode("utf-8")
        import base64, binascii
        b = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
        return f"t_{b}"
    except Exception:
        return None

def _base_pub() -> str:
    return os.getenv("STAS_GW_BASE", "https://intervals.stas.run/gw")

async def _http_json(session: aiohttp.ClientSession, url: str, bearer: Optional[str]) -> Dict[str, Any]:
    headers = {}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    async with session.get(url, headers=headers, timeout=30) as r:
        text = await r.text()
        try:
            return json.loads(text)
        except Exception:
            # Если вернулся не-JSON — отдадим как текст
            return {"raw": text, "status": r.status}

def _ok(data: Dict[str, Any], label: str) -> Tuple[Dict[str, Any], str]:
    return {"ok": True, **data}, f"{label}: ok"

def _err(code: str, msg: str, label: str) -> Tuple[Dict[str, Any], str]:
    return {"ok": False, "error": {"code": code, "message": msg[:500]}}, f"{label}: error"

# ------------------------ readers ------------------------

async def user_summary_fetch(arguments: Dict[str, Any]):
    """
    args: { user_id?: int, connection_id?: str }
    Если ни user_id, ни связка не определены — возвращаем ok:false с понятной причиной.
    """
    user_id = _get(arguments, "user_id")
    # (Примечание: если у вас есть хранилище линка по connection_id, подключите сюда.)
    bearer = _auth_bearer(user_id)
    if not bearer:
        return _err("not_linked", "user_id не указан и токен не найден; сначала выполните линк или передайте user_id", "user.summary.fetch")

    base = _base_pub()
    url = f"{base}/profile/summary"
    async with aiohttp.ClientSession() as s:
        data = await _http_json(s, url, bearer)
    return _ok({"summary": data}, "user.summary.fetch")

async def user_last_training_fetch(arguments: Dict[str, Any]):
    """
    args: {
      user_id?: int,
      oldest?: 'YYYY-MM-DD',
      newest?: 'YYYY-MM-DD',
      connection_id?: string
    }
    По умолчанию — последние 14 дней. Никогда не бросаем исключений наружу.
    """
    user_id = _get(arguments, "user_id")
    bearer = _auth_bearer(user_id)
    if not bearer:
        return _err("not_linked", "user_id не указан и токен не найден; сначала выполните линк или передайте user_id", "user.last_training.fetch")

    oldest = _get(arguments, "oldest")
    newest = _get(arguments, "newest")
    if not (oldest and newest):
        oldest, newest = _window_14d()

    base = _base_pub()
    url = f"{base}/trainings?oldest={oldest}&newest={newest}"

    async with aiohttp.ClientSession() as s:
        data = await _http_json(s, url, bearer)

    # Нормализуем ответ к предсказуемой форме
    if isinstance(data, list):
        payload = {"count": len(data), "items": data, "window": {"oldest": oldest, "newest": newest}}
    else:
        payload = {"data": data, "window": {"oldest": oldest, "newest": newest}}
    return _ok(payload, "user.last_training.fetch")
