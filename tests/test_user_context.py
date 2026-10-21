"""Nombre de pila y hora local de quien chatea, para la instrucción del agente."""

import datetime as dt

from agent_dashboards.user_context import day_part, first_name, state_values, turn_context

NOON_UTC = dt.datetime(2026, 10, 15, 15, 30, tzinfo=dt.UTC)  # jueves


def test_first_name():
    assert first_name("Lucía Fernanda Pérez Gómez") == "Lucía"
    assert first_name("hola@cesardemaurizio.dev") == "Hola"
    assert first_name("ana.perez@empresa.com") == "Ana"
    assert first_name("") == "" and first_name(None) == ""


def test_day_parts():
    assert [day_part(h) for h in (4, 5, 11, 12, 19, 20, 23)] == [
        "madrugada", "mañana", "mañana", "tarde", "tarde", "noche", "noche"]


def test_state_values_use_the_browser_offset():
    out = state_values({"user_name": "Lucía Pérez", "user_utc_offset": -180,
                        "user_tz": "America/Argentina/Buenos_Aires"}, now=NOON_UTC)
    assert out == {"user_first_name": "Lucía", "user_day_part": "tarde",
                   "user_local_time": "jueves 15/10 12:30 (America/Argentina/Buenos_Aires)"}
    late = state_values({"user_name": "Ana", "user_utc_offset": 540}, now=NOON_UTC)  # UTC+9
    assert late["user_day_part"] == "madrugada" and late["user_local_time"].endswith("(UTC+09:00)")


def test_state_values_without_data_still_fill_every_variable():
    out = state_values({})
    assert out["user_first_name"] == "la persona" and out["user_local_time"] == "desconocida"


def test_turn_context_carries_the_local_time_outside_the_system_prompt():
    block = turn_context({"user_name": "Ana", "user_utc_offset": -180}, now=NOON_UTC)
    assert block == "<turn-context>Hora local de la persona: jueves 15/10 12:30 (UTC-03:00). Momento del día: tarde.</turn-context>"
    assert turn_context({"user_name": "Ana"}) == ""
