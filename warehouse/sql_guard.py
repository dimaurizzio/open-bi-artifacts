"""Reglas forzadas sobre el SQL: solo lectura, parámetros y datasets permitidos.

Estas reglas no dependen de que el modelo se acuerde: las aplican el callback del
agente (exploración) y el servicio de artifacts (fuentes de dashboards). El
análisis es léxico, no un parser completo: la última barrera es IAM de BigQuery.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from .. import config


class SqlGuardError(ValueError):
    """La consulta viola una regla forzada."""


_PARAM_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|CALL|"
    r"EXECUTE|DECLARE|SET|BEGIN|COMMIT|ROLLBACK|EXPORT|LOAD|ASSERT|RAISE)\b",
    re.IGNORECASE,
)
_IDENT = r"(?:`[^`]+`|[A-Za-z0-9_][A-Za-z0-9_\-]*)"
_DOTTED = rf"{_IDENT}(?:\s*\.\s*{_IDENT})*"
_FROM_REF_RE = re.compile(
    rf"\b(?:FROM|JOIN|TABLE)\s+({_DOTTED})(?!\s*\()(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?",
    re.IGNORECASE,
)
_ANY_DOTTED_RE = re.compile(rf"(?<![\w`.\-])({_IDENT}(?:\s*\.\s*{_IDENT}){{2,}})")
_ALIAS_STOPWORDS = {
    "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "ON", "USING",
    "GROUP", "ORDER", "LIMIT", "UNION", "WINDOW", "QUALIFY", "HAVING", "FOR", "TABLESAMPLE",
    "PIVOT", "UNPIVOT", "EXCEPT", "INTERSECT", "WITH", "SELECT", "AS",
}


def _mask(sql: str, keep_backticks: bool) -> str:
    """Blanquea strings y comentarios (y backticks si se pide) conservando posiciones."""
    out = list(sql)
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        two = sql[i : i + 2]
        if two == "--" or c == "#":
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        elif two == "/*":
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = " " * (j - i)
            i = j
        elif c in "'\"":
            triple = sql[i : i + 3] if sql[i : i + 3] in ("'''", '"""') else None
            quote = triple or c
            j = i + len(quote)
            while j < n:
                if sql[j] == "\\":
                    j += 2
                    continue
                if sql.startswith(quote, j):
                    break
                j += 1
            end = min(n, j + len(quote))
            for k in range(i + len(quote), min(j, n)):
                out[k] = " "
            i = end
        elif c == "`":
            j = sql.find("`", i + 1)
            j = n - 1 if j < 0 else j
            if not keep_backticks:
                for k in range(i + 1, j):
                    out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def find_params(sql: str) -> list[str]:
    """Parámetros {{nombre}} usados por la consulta, validando que sean literales únicos."""
    masked = _mask(sql, keep_backticks=False)
    in_code = _PARAM_RE.findall(masked)
    if len(in_code) != len(_PARAM_RE.findall(sql)):
        raise SqlGuardError(
            "Un parámetro {{...}} aparece dentro de un string, comentario o identificador."
            " Los parámetros van como literal único: usá CONCAT('%', {{q}}, '%') en vez de"
            " '%{{q}}%'."
        )
    return sorted(set(in_code))


def check_readonly(sql: str) -> None:
    masked = _mask(_PARAM_RE.sub("NULL", sql), keep_backticks=False).strip()
    masked = masked.rstrip().rstrip(";").strip()
    if not masked:
        raise SqlGuardError("La consulta está vacía.")
    if ";" in masked:
        raise SqlGuardError("Se permite una sola sentencia SELECT o WITH.")
    first = re.match(r"\(*\s*([A-Za-z]+)", masked)
    if not first or first.group(1).upper() not in ("SELECT", "WITH"):
        raise SqlGuardError("La consulta debe empezar con SELECT o WITH.")
    bad = _FORBIDDEN_RE.search(masked)
    if bad:
        raise SqlGuardError(f"Palabra no permitida en una consulta de solo lectura: {bad.group(1).upper()}.")


def _blank_extract(masked: str) -> str:
    """Quita EXTRACT(... FROM col) e IS DISTINCT FROM, que no son tablas."""
    masked = re.sub(r"\bIS\s+(?:NOT\s+)?DISTINCT\s+FROM\b", lambda m: " " * len(m.group()), masked, flags=re.I)
    out = list(masked)
    for m in re.finditer(r"\bEXTRACT\s*\(", masked, flags=re.I):
        depth, k = 1, m.end()
        while k < len(masked) and depth:
            depth += {"(": 1, ")": -1}.get(masked[k], 0)
            k += 1
        out[m.start() : k] = " " * (k - m.start())
    return "".join(out)


def _parts(ref: str) -> list[str]:
    ref = re.sub(r"\s*\.\s*", ".", ref)
    parts: list[str] = []
    for token in re.findall(r"`[^`]+`|[^.`]+", ref):
        parts.extend(token.strip("`").split("."))
    return [p for p in parts if p]


_CLAUSE_RE = re.compile(
    r"\b(SELECT|FROM|JOIN|TABLE|WHERE|ON|USING|GROUP|HAVING|QUALIFY|WINDOW|ORDER|LIMIT|UNION|INTERSECT|EXCEPT)\b",
    re.IGNORECASE,
)
_FROM_CLAUSES = {"FROM", "JOIN", "TABLE"}


def _paren_stacks(masked: str) -> list[tuple[int, ...]]:
    """Para cada posición, los paréntesis abiertos que la encierran (su bloque)."""
    stacks: list[tuple[int, ...]] = []
    stack: list[int] = []
    for i, c in enumerate(masked):
        if c == "(":
            stack.append(i)
        stacks.append(tuple(stack))
        if c == ")" and stack:
            stack.pop()
    return stacks


def referenced_datasets(sql: str) -> set[str]:
    """Datasets ("proyecto.dataset") que lee la consulta (heurístico).

    Un nombre con puntos que empieza con un alias no es una tabla, sino un camino dentro
    de ese alias (`t.items`, `t.campo.sub`). Pero solo si el alias se ve desde ahí, como
    en BigQuery: un alias vale en su bloque de paréntesis y en los de adentro, nunca
    afuera; y dentro de un FROM, solo si se definió antes (un ítem no ve los alias de los
    que vienen después). Sin eso, `FROM proy.secreto.t CROSS JOIN (SELECT … AS proy)`
    escondería una tabla fuera del warehouse habilitado.
    """
    masked = _blank_extract(_mask(_PARAM_RE.sub("NULL", sql), keep_backticks=True))
    stacks = _paren_stacks(masked) + [()]
    clauses = [(m.start(), m.group(1).upper(), stacks[m.start()]) for m in _CLAUSE_RE.finditer(masked)]

    # Alias y nombres de tabla definidos por cada ítem de un FROM o JOIN: (nombre, posición, bloque).
    defs: list[tuple[str, int, tuple[int, ...]]] = []
    candidates: dict[int, str] = {}
    for m in _FROM_REF_RE.finditer(masked):
        ref, alias, pos = m.group(1), m.group(2), m.start(1)
        candidates[pos] = ref
        parts = _parts(ref)
        names = [alias] if alias and alias.upper() not in _ALIAS_STOPWORDS else []
        names.append(parts[-1] if parts else ref)  # sin alias, la tabla o CTE se llama por su nombre
        defs += [(n.lower(), pos, stacks[m.start()]) for n in names if n]
    for m in _ANY_DOTTED_RE.finditer(masked):
        candidates.setdefault(m.start(1), m.group(1))
    for m in re.finditer(r"`[^`]*\.[^`]*`", masked):
        candidates.setdefault(m.start(), m.group())

    def in_from(pos: int, block: tuple[int, ...]) -> bool:
        """¿La referencia está en un FROM/JOIN? Mira la última cláusula de su bloque o de uno de afuera."""
        for c_pos, kind, c_block in reversed(clauses):
            if c_pos < pos and block[: len(c_block)] == c_block:
                return kind in _FROM_CLAUSES
        return True  # sin cláusula conocida: se trata como tabla

    def alias_visible(name: str, pos: int, block: tuple[int, ...], from_item: bool) -> bool:
        for d_name, d_pos, d_block in defs:
            if d_name != name or d_pos == pos or block[: len(d_block)] != d_block:
                continue
            if not from_item or d_pos < pos:
                return True
        return False

    datasets: set[str] = set()
    for pos, ref in sorted(candidates.items()):
        parts = _parts(ref)
        if len(parts) < 2:
            continue
        block = stacks[pos]
        if alias_visible(parts[0].lower(), pos, block, in_from(pos, block)):
            continue
        upper = [p.upper() for p in parts]
        if parts[0].lower().startswith("region-"):
            datasets.add(f"{parts[0]}.*")
        elif len(parts) >= 3 and upper[2] == "INFORMATION_SCHEMA":
            datasets.add(f"{parts[0]}.{parts[1]}")
        elif upper[1] == "INFORMATION_SCHEMA":
            datasets.add(f"{config.WAREHOUSE_PROJECT}.{parts[0]}")
        elif len(parts) == 2:
            datasets.add(f"{config.WAREHOUSE_PROJECT}.{parts[0]}")
        else:
            datasets.add(f"{parts[0]}.{parts[1]}")
    return datasets


def check_datasets(sql: str) -> None:
    if not config.WAREHOUSE_DATASETS:
        return
    allowed = {d.lower() for d in config.WAREHOUSE_DATASETS}
    outside = sorted(d for d in referenced_datasets(sql) if d.lower() not in allowed)
    if outside:
        raise SqlGuardError(
            f"La consulta lee datasets fuera del warehouse habilitado: {', '.join(outside)}."
            f" Permitidos: {', '.join(config.WAREHOUSE_DATASETS)}. Usá nombres completos"
            " `proyecto.dataset.tabla`."
        )


def check_dataset_id(project: str | None, dataset: str | None) -> None:
    if not config.WAREHOUSE_DATASETS or not dataset:
        return
    key = f"{project or config.WAREHOUSE_PROJECT}.{dataset}".lower()
    if key not in {d.lower() for d in config.WAREHOUSE_DATASETS}:
        raise SqlGuardError(
            f"El dataset {project}.{dataset} no está habilitado. Permitidos:"
            f" {', '.join(config.WAREHOUSE_DATASETS)}."
        )


def check_query(sql: str) -> list[str]:
    """Todas las reglas forzadas. Devuelve los parámetros que usa la consulta."""
    params = find_params(sql)
    check_readonly(sql)
    check_datasets(sql)
    return params


# --- Parámetros ----------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _string_literal(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")
    )
    return f"'{escaped}'"


def literal(value: Any, typ: str = "text") -> str:
    """Convierte un valor de filtro en un literal SQL seguro de BigQuery."""
    if value is None or value == "" or value == []:
        # Tipado: BigQuery rechaza `col = NULL` con un NULL sin tipo.
        return {
            "number": "CAST(NULL AS FLOAT64)",
            "date": "CAST(NULL AS DATE)",
            "boolean": "CAST(NULL AS BOOL)",
            "multi": "CAST(NULL AS ARRAY<STRING>)",
            "multiselect": "CAST(NULL AS ARRAY<STRING>)",
        }.get(typ, "CAST(NULL AS STRING)")
    if typ in ("multi", "multiselect") or isinstance(value, list):
        items = value if isinstance(value, list) else [value]
        if not items:
            return "CAST([] AS ARRAY<STRING>)"
        return "[" + ", ".join(_string_literal(str(v)) for v in items) + "]"
    if typ == "boolean" or isinstance(value, bool):
        return "TRUE" if str(value).lower() in ("true", "1") else "FALSE"
    if typ == "number" or isinstance(value, (int, float)):
        try:
            num = float(value)
        except (TypeError, ValueError) as e:
            raise SqlGuardError(f"Valor numérico inválido: {value!r}") from e
        if num != num or num in (float("inf"), float("-inf")):
            raise SqlGuardError(f"Valor numérico inválido: {value!r}")
        return str(int(num)) if num.is_integer() else repr(num)
    if typ == "date":
        text = str(value)[:10]
        if not _DATE_RE.match(text):
            raise SqlGuardError(f"Fecha inválida (se espera AAAA-MM-DD): {value!r}")
        dt.date.fromisoformat(text)
        return f"DATE '{text}'"
    return _string_literal(str(value))


def render(sql: str, specs: dict[str, dict[str, Any]], values: dict[str, Any]) -> str:
    """Reemplaza cada {{nombre}} por su literal; valores ausentes toman el default."""

    def sub(m: re.Match) -> str:
        name = m.group(1)
        spec = specs.get(name)
        if spec is None:
            raise SqlGuardError(f"El parámetro {{{{{name}}}}} no está declarado en dash/params.")
        value = values.get(name, spec.get("default"))
        return literal(value, spec.get("type", "text"))

    find_params(sql)
    return _PARAM_RE.sub(sub, sql)


def params_key(values: dict[str, Any]) -> str:
    return json.dumps(values, sort_keys=True, default=str)


# --- Contrato 2 (dashboards en vivo) ------------------------------------------
# Palabras que el tipo rechaza fuera de comillas y comentarios, y largo máximo.
_FORBIDDEN_V2 = (
    "INSERT UPDATE DELETE MERGE DROP ALTER CREATE TRUNCATE GRANT REVOKE CALL EXEC "
    "EXECUTE COPY EXPORT LOAD INTO RETURNING DECLARE SET BEGIN COMMIT"
).split()
_FORBIDDEN_V2_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN_V2) + r")\b", re.IGNORECASE)
MAX_SQL_CHARS = 16_000


def check_sql_v2(sql: str) -> list[str]:
    """Reglas del tipo: una sentencia SELECT/WITH de hasta 16.000 caracteres, sin palabras
    prohibidas fuera de comillas/comentarios/backticks, parámetros como literal único y solo
    datasets habilitados. Devuelve los parámetros que usa."""
    if len(sql) > MAX_SQL_CHARS:
        raise SqlGuardError(f"El SQL tiene {len(sql)} caracteres; el máximo es {MAX_SQL_CHARS}.")
    params = find_params(sql)
    masked = _mask(_PARAM_RE.sub("NULL", sql), keep_backticks=False).strip().rstrip(";").strip()
    if not masked:
        raise SqlGuardError("La consulta está vacía.")
    if ";" in masked:
        raise SqlGuardError("Se permite una sola sentencia SELECT o WITH.")
    first = re.match(r"\(*\s*([A-Za-z]+)", masked)
    if not first or first.group(1).upper() not in ("SELECT", "WITH"):
        raise SqlGuardError("La consulta debe empezar con SELECT o WITH.")
    bad = _FORBIDDEN_V2_RE.search(masked)
    if bad:
        raise SqlGuardError(
            f"Palabra no permitida fuera de comillas y comentarios: {bad.group(1).upper()}"
            " (si es una columna, ponela entre backticks)."
        )
    check_datasets(sql)
    return params


def _quote(value: str) -> str:
    return _string_literal(value)


def literal_v2(value: Any) -> str:
    """UN literal sin tipo declarado: texto entre comillas, número, TRUE/FALSE, NULL o
    una lista como ('pro', 'max'). Una lista vacía no coincide con nada: (NULL)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):
            raise SqlGuardError(f"Número inválido: {value!r}")
        return str(int(value)) if float(value).is_integer() else repr(float(value))
    if isinstance(value, str):
        if "\n" in value or "\r" in value:
            raise SqlGuardError("Un valor de texto de un filtro va en una sola línea.")
        return _quote(value)
    if isinstance(value, list):
        if not value:
            return "(NULL)"
        return "(" + ", ".join(literal_v2(v) for v in value) + ")"
    raise SqlGuardError(f"Valor de filtro no admitido: {type(value).__name__}")


def clean_param_value(value: Any) -> Any:
    """Lo que setParams acepta: texto de una línea, número, booleano, null o lista de esos.
    Cualquier otra cosa se descarta (devuelve None)."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if "\n" not in value and "\r" not in value else None
    if isinstance(value, list) and all(v is None or isinstance(v, (bool, int, float, str)) for v in value):
        return [v for v in value if not (isinstance(v, str) and ("\n" in v or "\r" in v))]
    return None


def render_args_v2(args: dict[str, Any], values: dict[str, Any], sql_arg: str | None) -> dict[str, Any]:
    """Completa {{nombre}} en los argumentos de la llamada.

    - En el argumento de SQL: UN literal (texto entre comillas, número, TRUE/FALSE, NULL, lista).
    - Un argumento que es solo "{{nombre}}": el valor con su tipo.
    - Dentro de otro texto: texto plano (en BigQuery).
    Un parámetro sin valor vale NULL."""

    def fill(text: str, as_sql: bool) -> Any:
        whole = _PARAM_RE.fullmatch(text.strip())
        if whole and not as_sql:
            return values.get(whole.group(1))
        if as_sql:
            find_params(text)  # literal único: nunca dentro de comillas
            return _PARAM_RE.sub(lambda m: literal_v2(values.get(m.group(1))), text)
        return _PARAM_RE.sub(lambda m: "" if values.get(m.group(1)) is None else str(values.get(m.group(1))), text)

    out: dict[str, Any] = {}
    for key, val in (args or {}).items():
        out[key] = fill(val, key == sql_arg) if isinstance(val, str) else val
    return out


def params_in_args(args: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    for val in (args or {}).values():
        if isinstance(val, str):
            names.update(_PARAM_RE.findall(val))
    return sorted(names)
