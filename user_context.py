"""Quién está del otro lado del chat y qué hora es para esa persona.

El servidor guarda en el estado de la sesión el nombre de la cuenta de Google
(`user_name`) y el desfasaje horario del navegador (`user_utc_offset`, en minutos)
con cada mensaje. Acá se convierte en lo que interpola la instrucción del agente.
Se usa el desfasaje y no el nombre de la zona para no depender de la base de zonas
horarias (Python en Windows no la trae).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

_DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def first_name(name: str | None) -> str:
    """'Lucía Fernanda Pérez' → 'Lucía'; un mail → la parte de antes de la @."""
    text = (name or "").strip()
    if "@" in text:
        text = text.split("@", 1)[0].replace(".", " ").replace("_", " ")
    word = text.split()[0] if text.split() else ""
    return word[:1].upper() + word[1:] if word else ""


def day_part(hour: int) -> str:
    if 5 <= hour < 12:
        return "mañana"
    if 12 <= hour < 20:
        return "tarde"
    if 20 <= hour < 24:
        return "noche"
    return "madrugada"


def local_now(utc_offset_min: int | None, now: dt.datetime | None = None) -> dt.datetime:
    now = now or dt.datetime.now(dt.UTC)
    offset = dt.timedelta(minutes=utc_offset_min or 0)
    return now.astimezone(dt.timezone(offset))


def state_values(state: dict[str, Any], now: dt.datetime | None = None) -> dict[str, str]:
    """Variables para la instrucción: nombre de pila, hora local y momento del día."""
    offset = state.get("user_utc_offset")
    try:
        offset = int(offset) if offset is not None else None
    except (TypeError, ValueError):
        offset = None
    if offset is None:
        return {
            "user_first_name": first_name(state.get("user_name")) or "la persona",
            "user_local_time": "desconocida",
            "user_day_part": "desconocido",
        }
    local = local_now(offset, now)
    sign = "+" if offset >= 0 else "-"
    zone = state.get("user_tz") or f"UTC{sign}{abs(offset) // 60:02d}:{abs(offset) % 60:02d}"
    return {
        "user_first_name": first_name(state.get("user_name")) or "la persona",
        "user_local_time": f"{_DAYS[local.weekday()]} {local:%d/%m %H:%M} ({zone})",
        "user_day_part": day_part(local.hour),
    }


def turn_context(state: dict[str, Any], now: dt.datetime | None = None) -> str:
    """Bloque con la hora local que viaja delante de cada mensaje (vacío si no se sabe).

    Va en el mensaje y no en la instrucción: el prompt de sistema tiene que quedar
    igual en toda la conversación, porque los modelos que atan su razonamiento al
    prompt (thinking preservado) rechazan el historial si cambia, y la caché se pierde.
    """
    values = state_values(state, now)
    if values["user_local_time"] == "desconocida":
        return ""
    return (
        f"<turn-context>Hora local de la persona: {values['user_local_time']}. "
        f"Momento del día: {values['user_day_part']}.</turn-context>"
    )
