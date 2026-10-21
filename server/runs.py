"""Turnos del agente desacoplados de la conexión HTTP.

Cada turno corre en su propia tarea de asyncio y deja sus eventos en un buffer.
El SSE del navegador solo lee ese buffer: si la conexión se corta (cambiar de
chat, recargar, cerrar la pestaña) el agente sigue hasta terminar, y otra
conexión se puede enganchar y repetir el turno desde el principio.

Vive en memoria del proceso: alcanza para un servidor; con varios workers haría
falta un broker (Redis, Pub/Sub).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent_dashboards.runs")

# Cuánto se guarda un turno terminado, para quien se reconecte justo al final.
_KEEP_FINISHED_SECONDS = 120
# Cada cuánto se manda un comentario SSE si el turno está callado. Los proxies
# (Cloudflare corta a los ~100 s sin datos) cierran las conexiones mudas.
SSE_PING_SECONDS = 20


@dataclass
class ChatRun:
    chat_id: str
    started_at: float = field(default_factory=time.time)
    items: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    task: asyncio.Task | None = None
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def push(self, item: dict[str, Any]) -> None:
        self.items.append(item)
        async with self._changed:
            self._changed.notify_all()

    async def finish(self) -> None:
        self.done = True
        async with self._changed:
            self._changed.notify_all()

    async def follow(self) -> AsyncIterator[dict[str, Any]]:
        """Todos los items del turno desde el primero, y los nuevos a medida que llegan."""
        idx = 0
        while True:
            while idx < len(self.items):
                yield self.items[idx]
                idx += 1
            if self.done:
                return
            async with self._changed:
                await self._changed.wait_for(lambda: idx < len(self.items) or self.done)


async def sse_events(run: ChatRun, ping_seconds: float = SSE_PING_SECONDS) -> AsyncIterator[str]:
    """El turno como eventos SSE, con un comentario `: ping` cuando pasa un rato sin nada.

    Los items pasan por una cola para poder esperar con timeout sin cancelar el
    generador del turno (cancelar `follow()` a la mitad lo cerraría).
    """
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for item in run.follow():
                await queue.put(item)
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=ping_seconds)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
        yield 'data: {"type": "done"}\n\n'
    finally:
        task.cancel()


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, ChatRun] = {}

    def active(self, chat_id: str) -> ChatRun | None:
        run = self._runs.get(chat_id)
        return run if run and not run.done else None

    def get(self, chat_id: str) -> ChatRun | None:
        return self._runs.get(chat_id)

    def running_ids(self) -> set[str]:
        return {cid for cid, run in self._runs.items() if not run.done}

    def start(
        self,
        chat_id: str,
        first_items: list[dict[str, Any]],
        produce: Callable[[ChatRun], Awaitable[None]],
    ) -> ChatRun:
        if self.active(chat_id):
            raise RuntimeError("Ya hay un turno en curso en este chat.")
        run = ChatRun(chat_id=chat_id, items=list(first_items))
        self._runs[chat_id] = run

        async def main() -> None:
            try:
                await produce(run)
            except asyncio.CancelledError:
                await run.push({"type": "cancelled"})
            except Exception as e:  # noqa: BLE001 - se informa al front
                logger.exception("Falló el turno del chat %s", chat_id)
                await run.push({"type": "error", "message": f"{type(e).__name__}: {e}"})
            finally:
                await run.finish()
                asyncio.get_running_loop().call_later(_KEEP_FINISHED_SECONDS, self._forget, chat_id, run)

        run.task = asyncio.create_task(main(), name=f"chat-run-{chat_id}")
        return run

    def cancel(self, chat_id: str) -> bool:
        run = self.active(chat_id)
        if run and run.task:
            run.task.cancel()
            return True
        return False

    def _forget(self, chat_id: str, run: ChatRun) -> None:
        if self._runs.get(chat_id) is run:
            del self._runs[chat_id]


runs = RunManager()
