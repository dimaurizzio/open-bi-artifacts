"""Limpieza periódica: sesiones de login vencidas y chats viejos (CHAT_RETENTION_DAYS).

ADK guarda cada chat entero (mensajes, llamadas a herramientas y sus resultados,
incluidos resultados de SQL). Con CHAT_RETENTION_DAYS > 0, un chat sin actividad
en ese plazo se borra; uno con un turno en curso nunca. Las copias compartidas en
un proyecto no se borran con el chat: las borra quien las compartió o un owner.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from google.adk.sessions.base_session_service import BaseSessionService

from .. import config
from ..auth.store import AppStore

logger = logging.getLogger("agent_dashboards.retention")

INTERVAL_SECONDS = 6 * 3600


async def sweep(sessions: BaseSessionService, store: AppStore, running: Callable[[], set[str]]) -> dict[str, int]:
    out = {"login_sessions": store.purge_expired_sessions(), "chats": 0}
    if config.CHAT_RETENTION_DAYS <= 0:
        return out
    cutoff = time.time() - config.CHAT_RETENTION_DAYS * 86400
    busy = running()
    res = await sessions.list_sessions(app_name=config.APP_NAME)
    for s in res.sessions:
        if s.last_update_time < cutoff and s.id not in busy:
            await sessions.delete_session(app_name=config.APP_NAME, user_id=s.user_id, session_id=s.id)
            out["chats"] += 1
    return out


async def loop(sessions: BaseSessionService, store: AppStore, running: Callable[[], set[str]]) -> None:
    while True:
        try:
            out = await sweep(sessions, store, running)
            if any(out.values()):
                logger.info("Limpieza: %s", out)
        except Exception:  # noqa: BLE001 - la limpieza nunca tira abajo el servidor
            logger.exception("Falló la limpieza periódica")
        await asyncio.sleep(INTERVAL_SECONDS)
