"""Reglas del conocimiento: quién ve qué, quién aprueba, validación y procedencia.

- Lo privado lo ve solo su autor y no necesita aprobación.
- Lo compartido entra como `proposed` y lo aprueba un curador
  (`config.KNOWLEDGE_CURATORS`). Mientras tanto se usa, marcado como no aprobado.
- El SQL pasa por las guardas de `sql_guard` al proponerse y por un dry run al
  aprobarse (o al guardarse, si es una nota privada).
- Prioridad al leer: compartido aprobado > notas privadas del usuario > catálogo >
  propuestas sin aprobar.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from .. import config
from ..warehouse import sql_guard
from ..warehouse.mcp_client import BigQueryMcpClient, WarehouseError
from ..warehouse.mcp_client import client as default_client
from . import catalog_sync, okf_export
from .store import KINDS, TARGET_TYPES, VISIBILITIES, KnowledgeStore, clean_ref

logger = logging.getLogger("agent_dashboards.knowledge")

MAX_CONTENT_CHARS = 4000
MAX_SOURCES = 10
# Tipos de conocimiento que no tienen sentido sin su SQL.
NEEDS_SQL = {"definicion_metrica", "consulta_ejemplo"}
ACTIVE = ("approved", "proposed")

KIND_LABELS = {
    "descripcion": "Descripción",
    "definicion_metrica": "Definición de métrica",
    "regla_filtro": "Regla de filtro",
    "advertencia": "Advertencia",
    "join": "Join",
    "sinonimo": "Sinónimo",
    "consulta_ejemplo": "Consulta de ejemplo",
}
TARGET_LABELS = {"table": "tabla", "column": "columna", "metric": "métrica", "term": "término", "join": "join"}
PRIORITY_NOTE = "compartido aprobado > notas privadas tuyas > catálogo > propuestas sin aprobar"


class KnowledgeError(ValueError):
    """Pedido rechazado. `code` sirve para elegir el estado HTTP."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def today() -> dt.date:
    return dt.date.today()


class KnowledgeService:
    def __init__(
        self,
        store: KnowledgeStore | None = None,
        warehouse: BigQueryMcpClient = default_client,
        okf_dir=None,
        auto_export: bool | None = None,
    ):
        self._store = store
        self.warehouse = warehouse
        self.okf_dir = okf_dir or config.KNOWLEDGE_OKF_DIR
        self.auto_export = config.KNOWLEDGE_OKF_AUTO if auto_export is None else auto_export

    @property
    def store(self) -> KnowledgeStore:
        # Se abre al primer uso: importar el módulo no crea la base.
        if self._store is None:
            self._store = KnowledgeStore()
        return self._store

    # --- permisos y presentación ------------------------------------------------

    # El servidor lo reemplaza para que los admins también puedan aprobar.
    extra_curator: Any = staticmethod(lambda user: False)

    def is_curator(self, user: str) -> bool:
        return user in config.KNOWLEDGE_CURATORS or bool(self.extra_curator(user))

    def visible(self, e: dict[str, Any], user: str) -> bool:
        if e["author_id"] == user:
            return True
        if e["visibility"] != "shared":
            return False
        # Lo rechazado y lo deprecado de otros lo ven los curadores (para revisar la historia).
        return e["status"] in ACTIVE or self.is_curator(user)

    @staticmethod
    def priority(e: dict[str, Any]) -> int:
        """0 compartido aprobado, 1 nota privada, (2 es el catálogo), 3 propuesta, 9 inactiva."""
        if e["status"] not in ACTIVE:
            return 9
        if e["visibility"] == "private":
            return 1
        return 0 if e["status"] == "approved" else 3

    @staticmethod
    def is_stale(e: dict[str, Any]) -> bool:
        return bool(e.get("stale_after")) and e["stale_after"] < today().isoformat()

    def provenance(self, e: dict[str, Any], user: str) -> str:
        day = lambda s: (s or "")[:10]  # noqa: E731
        author = "vos" if e["author_id"] == user else e["author_id"]
        if e["visibility"] == "private":
            label = "nota privada tuya" if e["author_id"] == user else f"nota privada de {e['author_id']}"
            parts = [label, day(e["created_at"])]
        elif e["status"] == "approved":
            parts = ["compartido", f"autor {author}", f"aprobado por {e['reviewed_by']}", day(e["reviewed_at"])]
        elif e["status"] == "proposed":
            parts = [f"propuesto por {author}, sin aprobar", day(e["created_at"])]
        elif e["status"] == "rejected":
            parts = [f"rechazado por {e['reviewed_by']}", f"autor {author}", day(e["reviewed_at"])]
        else:
            parts = ["compartido", f"autor {author}"]
        if e["status"] == "deprecated":
            parts.append("deprecada")
        if e.get("proposed_via") == "agent":
            parts.append("vía agente")
        if self.is_stale(e) and e["status"] in ACTIVE:
            parts.append(f"vencida desde {e['stale_after']}")
        return "[" + " · ".join(parts) + "]"

    def present(self, e: dict[str, Any], user: str) -> dict[str, Any]:
        curator = self.is_curator(user)
        own_private = e["visibility"] == "private" and e["author_id"] == user
        active = e["status"] in ACTIVE
        return {
            **e,
            "provenance": self.provenance(e, user),
            "priority": self.priority(e),
            "stale": self.is_stale(e),
            "can_review": curator and e["visibility"] == "shared" and e["status"] == "proposed",
            "can_deprecate": active and (own_private or (e["visibility"] == "shared" and curator)),
            "can_supersede": active and (e["visibility"] == "shared" or own_private),
        }

    def _for_agent(self, e: dict[str, Any], user: str) -> dict[str, Any]:
        p = self.present(e, user)
        out = {
            "id": p["id"],
            "procedencia": p["provenance"],
            "sobre": f"{TARGET_LABELS.get(p['target_type'], p['target_type'])} {p['target_ref']}",
            "tipo": p["kind"],
            "contenido": p["content"],
        }
        if p.get("sql"):
            out["sql"] = p["sql"]
        if p.get("sources"):
            out["fuentes"] = [s.get("title") or s.get("resource") for s in p["sources"]]
        if p["stale"]:
            out["vencida"] = f"Venció el {p['stale_after']}: no la apliques sin avisar."
        return out

    # --- validación -----------------------------------------------------------------

    def _resolve_ref(self, target_type: str, ref: str) -> tuple[str, list[str]]:
        """Completa el nombre con el catálogo cuando es corto o le falta el proyecto."""
        warnings: list[str] = []
        ref = clean_ref(ref)
        if not ref:
            raise KnowledgeError("Falta target_ref: sobre qué tabla, columna, métrica o término es.")
        if target_type not in ("table", "column"):
            return ref, warnings
        table_part, column = (ref.rsplit(".", 1) + [""])[:2] if target_type == "column" else (ref, "")
        if target_type == "column" and not column:
            raise KnowledgeError("Una columna va como proyecto.dataset.tabla.columna.")
        matches = self.store.catalog(table_part)
        if len(matches) == 1:
            table_part = matches[0]["ref"]
            if column:
                names = {c["name"].lower() for c in matches[0]["columns"]}
                if names and column.lower() not in names:
                    raise KnowledgeError(f"La tabla {table_part} no tiene la columna {column}.")
        elif len(matches) > 1:
            raise KnowledgeError(
                f"'{table_part}' es ambiguo: " + ", ".join(m["ref"] for m in matches[:5]) + ". Usá el nombre completo."
            )
        else:
            self._require_enabled(table_part)
            warnings.append(f"{table_part} no está en el catálogo (¿falta sincronizarlo?).")
        return (f"{table_part}.{column}" if column else table_part), warnings

    def _require_enabled(self, table_ref: str) -> None:
        """El conocimiento de tablas va sobre las que el agente puede consultar.

        Si alguien pasa la ficha de una tabla de origen (otro proyecto, la que lee una
        vista), guardarla con ese nombre la deja huérfana: knowledge_get de la tabla
        habilitada no la encuentra. Se rechaza y se sugiere la del catálogo con el mismo nombre.
        """
        enabled = {d.lower() for d in config.WAREHOUSE_DATASETS}
        if not enabled:
            return  # sin restricción de datasets, cualquier tabla vale
        parts = table_ref.split(".")
        if len(parts) == 3:
            ok = f"{parts[0]}.{parts[1]}".lower() in enabled
        elif len(parts) == 2:
            ok = parts[0].lower() in {d.split(".", 1)[1] for d in enabled}
        else:
            ok = False
        if ok:
            return
        same_name = [t["ref"] for t in self.store.catalog(parts[-1])]
        hint = (
            f" ¿Es {' o '.join(same_name[:3])}? Si la ficha es de la tabla de origen que lee esa,"
            " guardalo sobre la habilitada y nombrá el origen en el contenido o en las fuentes."
            if same_name else f" Los datasets habilitados son: {', '.join(config.WAREHOUSE_DATASETS)}."
        )
        raise KnowledgeError(f"{table_ref} no está en los datasets habilitados.{hint}")

    @staticmethod
    def _sources(raw: Any) -> list[dict[str, str]]:
        if raw in (None, "", []):
            return []
        items = raw if isinstance(raw, list) else [raw]
        out = []
        for s in items[:MAX_SOURCES]:
            if isinstance(s, dict):
                resource = str(s.get("resource") or s.get("url") or "").strip()
                title = str(s.get("title") or "").strip()
            else:
                resource, title = str(s).strip(), ""
            if not resource and not title:
                continue
            out.append({k: v for k, v in (("resource", resource or title), ("title", title)) if v})
        return out

    def validate(self, draft: dict[str, Any], user: str) -> tuple[dict[str, Any], list[str]]:
        """Normaliza y valida una propuesta sin escribir nada. Devuelve la entrada y advertencias."""
        target_type = str(draft.get("target_type") or "").strip()
        if target_type not in TARGET_TYPES:
            raise KnowledgeError(f"target_type inválido. Válidos: {', '.join(TARGET_TYPES)}.")
        kind = str(draft.get("kind") or "").strip()
        if kind not in KINDS:
            raise KnowledgeError(f"kind inválido. Válidos: {', '.join(KINDS)}.")
        visibility = str(draft.get("visibility") or "private").strip()
        if visibility not in VISIBILITIES:
            raise KnowledgeError("visibility es 'private' (solo vos) o 'shared' (todos, con aprobación).")
        content = str(draft.get("content") or "").strip()
        if not content:
            raise KnowledgeError("Falta el contenido.")
        if len(content) > MAX_CONTENT_CHARS:
            raise KnowledgeError(f"El contenido tiene {len(content)} caracteres; el máximo es {MAX_CONTENT_CHARS}.")
        ref, warnings = self._resolve_ref(target_type, str(draft.get("target_ref") or ""))

        sql = str(draft.get("sql") or "").strip() or None
        if kind in NEEDS_SQL and not sql:
            raise KnowledgeError(f"Una entrada de tipo {kind} necesita su SQL.")
        if sql:
            if len(sql) > sql_guard.MAX_SQL_CHARS:
                raise KnowledgeError(f"El SQL tiene {len(sql)} caracteres; el máximo es {sql_guard.MAX_SQL_CHARS}.")
            try:
                if sql_guard.find_params(sql):
                    raise KnowledgeError("El SQL del conocimiento no usa parámetros {{...}}: escribí valores literales.")
                sql_guard.check_readonly(sql)
                sql_guard.check_datasets(sql)
            except sql_guard.SqlGuardError as e:
                raise KnowledgeError(f"SQL rechazado: {e}") from e

        stale_after = str(draft.get("stale_after") or "").strip()[:10] or None
        if stale_after:
            try:
                when = dt.date.fromisoformat(stale_after)
            except ValueError as e:
                raise KnowledgeError("stale_after va como AAAA-MM-DD.") from e
            if when <= today():
                raise KnowledgeError("stale_after tiene que ser una fecha futura.")

        supersedes_id = str(draft.get("supersedes_id") or "").strip() or None
        if supersedes_id:
            old = self.store.get(supersedes_id)
            if not old or not self.visible(old, user):
                raise KnowledgeError(f"No existe la entrada {supersedes_id}.", "missing")
            if old["status"] not in ACTIVE:
                raise KnowledgeError(f"La entrada {supersedes_id} ya no está vigente ({old['status']}).")
            if old["target_ref"].lower() != ref.lower() or old["target_type"] != target_type:
                raise KnowledgeError(f"Una corrección tiene que ser sobre lo mismo: {old['target_type']} {old['target_ref']}.")
            if old["visibility"] != visibility:
                raise KnowledgeError(
                    "Una corrección mantiene la visibilidad de la original"
                    f" ({'compartida' if old['visibility'] == 'shared' else 'privada'})."
                )
            if old["visibility"] == "private" and old["author_id"] != user:
                raise KnowledgeError("Solo el autor corrige una nota privada.", "forbidden")

        for e in self.store.entries(target=ref, include_children=False, statuses=ACTIVE):
            same = e["kind"] == kind and e["content"].strip().lower() == content.lower() and (e.get("sql") or None) == sql
            if same and self.visible(e, user) and e["id"] != supersedes_id:
                raise KnowledgeError(f"Ya existe esa entrada ({e['id']}, {self.provenance(e, user)}).")

        entry = {
            "target_type": target_type,
            "target_ref": ref,
            "kind": kind,
            "content": content,
            "sql": sql,
            "sources": self._sources(draft.get("sources")),
            "stale_after": stale_after,
            "visibility": visibility,
            "supersedes_id": supersedes_id,
        }
        return entry, warnings

    async def _dry_run(self, sql: str) -> dict[str, Any]:
        try:
            scanned = await self.warehouse.dry_run(sql)
        except WarehouseError as e:
            raise KnowledgeError(f"El SQL no pasa el dry run de BigQuery: {e}") from e
        out: dict[str, Any] = {"bytes": scanned}
        if scanned > config.MAX_BYTES_SCANNED:
            out["warning"] = (
                f"El SQL escanearía {scanned / 1024**3:.1f} GiB, más que el límite de"
                f" {config.MAX_BYTES_SCANNED / 1024**3:.0f} GiB por consulta: correrlo tal cual va a fallar."
            )
        return out

    # --- escritura --------------------------------------------------------------------

    async def propose(
        self,
        draft: dict[str, Any],
        user: str,
        *,
        proposed_via: str = "manual",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        entry, warnings = self.validate(draft, user)
        private = entry["visibility"] == "private"
        if private and entry["sql"]:
            check = await self._dry_run(entry["sql"])
            if check.get("warning"):
                warnings.append(check["warning"])
        saved = self.store.insert(
            {
                **entry,
                "author_id": user,
                "proposed_via": "agent" if proposed_via == "agent" else "manual",
                "source_session_id": session_id,
                "status": "approved" if private else "proposed",
            },
            actor=user,
        )
        if private and saved.get("supersedes_id"):
            self.store.set_status(saved["supersedes_id"], "deprecated", user, action="superseded", note=saved["id"])
        return {"ok": True, "entry": self.present(saved, user), "warnings": warnings}

    async def review(self, entry_id: str, user: str, approve: bool, note: str | None = None) -> dict[str, Any]:
        if not self.is_curator(user):
            raise KnowledgeError("Solo un curador aprueba o rechaza lo compartido.", "forbidden")
        e = self.store.get(entry_id)
        if not e:
            raise KnowledgeError(f"No existe la entrada {entry_id}.", "missing")
        if e["visibility"] != "shared" or e["status"] != "proposed":
            raise KnowledgeError(f"La entrada {entry_id} no está pendiente de revisión ({e['status']}).")
        warnings: list[str] = []
        note = (note or "").strip()[:1000] or None
        if approve:
            if e.get("sql"):
                try:
                    sql_guard.check_readonly(e["sql"])
                    sql_guard.check_datasets(e["sql"])
                except sql_guard.SqlGuardError as err:
                    raise KnowledgeError(f"SQL rechazado: {err}") from err
                check = await self._dry_run(e["sql"])
                if check.get("warning"):
                    warnings.append(check["warning"])
            e = self.store.set_status(entry_id, "approved", user, note=note, review=True)
            old = self.store.get(e["supersedes_id"]) if e.get("supersedes_id") else None
            if old and old["status"] in ACTIVE:
                self.store.set_status(old["id"], "deprecated", user, action="superseded", note=entry_id)
            self._export_quietly()
        else:
            e = self.store.set_status(entry_id, "rejected", user, note=note, review=True)
        return {"ok": True, "entry": self.present(e, user), "warnings": warnings}

    def deprecate(self, entry_id: str, user: str, note: str | None = None) -> dict[str, Any]:
        e = self.store.get(entry_id)
        if not e or not self.visible(e, user):
            raise KnowledgeError(f"No existe la entrada {entry_id}.", "missing")
        if not self.present(e, user)["can_deprecate"]:
            who = "su autor" if e["visibility"] == "private" else "un curador"
            raise KnowledgeError(f"Esta entrada la depreca {who}, y tiene que estar vigente.", "forbidden")
        e = self.store.set_status(entry_id, "deprecated", user, note=(note or "").strip()[:1000] or None)
        if e["visibility"] == "shared":
            self._export_quietly()
        return {"ok": True, "entry": self.present(e, user)}

    # --- lectura ------------------------------------------------------------------------

    def _visible_active(self, entries: list[dict[str, Any]], user: str) -> list[dict[str, Any]]:
        return [e for e in entries if e["status"] in ACTIVE and self.visible(e, user)]

    @staticmethod
    def _sort(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_date = sorted(entries, key=lambda e: (e["created_at"], e["seq"]), reverse=True)
        return sorted(by_date, key=KnowledgeService.priority)

    def lookup(self, ref: str, user: str) -> dict[str, Any]:
        """Todo lo que hay sobre una tabla (con sus columnas), métrica o término."""
        ref = clean_ref(ref)
        tables = self.store.catalog(ref)
        target = tables[0]["ref"] if len(tables) == 1 else ref
        entries = self._sort(self._visible_active(self.store.entries(target=target), user))
        # Primero lo de la tabla y después cada columna junta (sorted es estable: se mantiene la prioridad).
        entries.sort(key=lambda e: (e["target_type"] == "column", e["target_ref"].lower() if e["target_type"] == "column" else ""))
        return {"target": target, "catalog": tables, "entries": [self.present(e, user) for e in entries]}

    def lookup_for_agent(self, ref: str, user: str) -> dict[str, Any]:
        found = self.lookup(ref, user)
        tables = found["catalog"]
        out: dict[str, Any] = {"objetivo": found["target"], "orden_de_prioridad": PRIORITY_NOTE}
        if len(tables) > 1:
            out["ambiguo"] = [t["ref"] for t in tables[:10]]
        elif tables:
            out["catalogo"] = _catalog_for_agent(tables[0])
        entries = found["entries"]
        out["conocimiento"] = [self._for_agent(e, user) for e in entries]
        if not entries and not tables:
            out["nota"] = "No hay nada guardado sobre esto. Probá knowledge_search o el esquema con get_table_info."
        if any(e["stale"] for e in entries):
            out["vencidas"] = "Hay entradas vencidas: avisá antes de usarlas."
        return out

    def search(self, text: str, user: str, limit: int = 10) -> dict[str, Any]:
        hits = self.store.search_entries(text, limit=200)
        score = dict(hits)
        found = self._visible_active(self.store.entries(ids=[h[0] for h in hits]), user)
        found.sort(key=lambda e: score.get(e["id"], 0))
        entries = [self.present(e, user) for e in found[:limit]]
        tables = []
        for ref, _ in self.store.search_catalog(text, limit=5):
            t = self.store.catalog(ref)
            if t:
                tables.append(t[0])
        return {"entries": entries, "catalog": tables}

    def search_for_agent(self, text: str, user: str) -> dict[str, Any]:
        found = self.search(text, user)
        out: dict[str, Any] = {
            "conocimiento": [self._for_agent(e, user) for e in found["entries"]],
            "tablas_del_catalogo": [
                {"tabla": t["ref"], "descripcion": t.get("description") or "", "filas": t.get("num_rows")}
                for t in found["catalog"]
            ],
            "orden_de_prioridad": PRIORITY_NOTE,
        }
        if not found["entries"] and not found["catalog"]:
            out["nota"] = "Sin resultados. Probá con otras palabras o mirá el esquema con get_table_info."
        return out

    def list(
        self,
        user: str,
        *,
        status: str | None = None,
        target: str | None = None,
        mine: bool = False,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        statuses = (status,) if status else (None if include_inactive else ACTIVE)
        entries = [e for e in self.store.entries(target=target, statuses=statuses) if self.visible(e, user)]
        if mine:
            entries = [e for e in entries if e["author_id"] == user]
        return [self.present(e, user) for e in self._sort(entries)]

    def history(self, entry_id: str, user: str) -> dict[str, Any]:
        e = self.store.get(entry_id)
        if not e or not self.visible(e, user):
            raise KnowledgeError(f"No existe la entrada {entry_id}.", "missing")
        chain = [x for x in self.store.chain(entry_id) if self.visible(x, user)]
        events = self.store.events([x["id"] for x in chain])
        return {"entries": [self.present(x, user) for x in chain], "events": events}

    def overview(self, user: str) -> dict[str, Any]:
        """Para el panel: tablas del catálogo y conceptos documentados, con conteos."""
        counts: dict[str, dict[str, Any]] = {}
        for e in self._visible_active(self.store.entries(statuses=ACTIVE), user):
            ref = e["target_ref"]
            if e["target_type"] == "column":
                ref = ref.rsplit(".", 1)[0]
            c = counts.setdefault(
                ref.lower(),
                {"ref": ref, "target_type": "table" if e["target_type"] == "column" else e["target_type"],
                 "approved": 0, "private": 0, "proposed": 0, "stale": 0},
            )
            c[{0: "approved", 1: "private", 3: "proposed"}[self.priority(e)]] += 1
            c["stale"] += int(self.is_stale(e))
        tables = []
        for t in self.store.catalog():
            c = counts.pop(t["ref"].lower(), None) or {"approved": 0, "private": 0, "proposed": 0, "stale": 0}
            tables.append({
                "ref": t["ref"], "name": t["name"], "dataset": t["dataset"], "description": t.get("description") or "",
                "table_type": t.get("table_type"), "num_rows": t.get("num_rows"),
                **{k: c[k] for k in ("approved", "private", "proposed", "stale")},
            })
        others = sorted(counts.values(), key=lambda c: (c["target_type"], c["ref"].lower()))
        pending = sum(
            1 for e in self.store.entries(statuses=("proposed",), visibility="shared") if self.visible(e, user)
        )
        return {
            "user": user,
            "curator": self.is_curator(user),
            "tables": tables,
            "others": others,
            "pending": pending,
            "catalog_sync": self.store.get_meta("catalog_sync"),
            "export": self.store.get_meta("okf_export"),
        }

    def index_text(self, user: str, max_lines: int = config.KB_INDEX_MAX_LINES) -> str:
        """Índice corto para la skill: qué está documentado, una línea por cada cosa."""
        ov = self.overview(user)
        rows = [t | {"target_type": "table"} for t in ov["tables"] if t["approved"] + t["private"] + t["proposed"]]
        rows += ov["others"]
        lines = []
        for r in rows[:max_lines]:
            parts = []
            if r["approved"]:
                parts.append(f"{r['approved']} compartida{'s' if r['approved'] > 1 else ''}")
            if r["private"]:
                parts.append(f"{r['private']} nota{'s' if r['private'] > 1 else ''} tuya{'s' if r['private'] > 1 else ''}")
            if r["proposed"]:
                parts.append(f"{r['proposed']} sin aprobar")
            if r["stale"]:
                parts.append(f"{r['stale']} vencida{'s' if r['stale'] > 1 else ''}")
            lines.append(f"- `{r['ref']}` ({TARGET_LABELS.get(r['target_type'], r['target_type'])}): {', '.join(parts)}")
        if len(rows) > max_lines:
            lines.append(f"- … y {len(rows) - max_lines} más (usá knowledge_search)")
        sync = ov["catalog_sync"] or {}
        n_tables = len(ov["tables"])
        catalog = (
            f"Catálogo de BigQuery: {n_tables} tablas, sincronizado el {str(sync.get('at', ''))[:10]}."
            if n_tables else "Catálogo de BigQuery: todavía sin sincronizar."
        )
        if not lines:
            return catalog + " Todavía no hay conocimiento de personas guardado."
        return catalog + " Documentado:\n" + "\n".join(lines)

    # --- catálogo y exportación ---------------------------------------------------------

    async def sync_catalog(self, datasets: list[str] | None = None) -> dict[str, Any]:
        result = await catalog_sync.sync(self.store, self.warehouse, datasets)
        self._export_quietly()
        return result

    def catalog_age_hours(self) -> float | None:
        sync = self.store.get_meta("catalog_sync") or {}
        if not sync.get("at"):
            return None
        at = dt.datetime.fromisoformat(sync["at"].replace("Z", "+00:00"))
        return (dt.datetime.now(dt.UTC) - at).total_seconds() / 3600

    def export(self, out_dir=None) -> dict[str, Any]:
        result = okf_export.export_bundle(self.store, out_dir or self.okf_dir)
        self.store.set_meta("okf_export", result)
        return result

    def _export_quietly(self) -> None:
        if not self.auto_export:
            return
        try:
            self.export()
        except Exception:  # noqa: BLE001 - la exportación nunca frena una aprobación
            logger.exception("No se pudo exportar el bundle OKF")


def _catalog_for_agent(t: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "procedencia": f"[catálogo de BigQuery · sincronizado {str(t.get('synced_at', ''))[:10]}]",
        "tabla": t["ref"],
        "tipo": t.get("table_type"),
        "descripcion": t.get("description") or "",
        "filas": t.get("num_rows"),
        "actualizada": t.get("last_modified"),
    }
    if t.get("partition_field") or t.get("partition_type"):
        out["particion"] = f"{t.get('partition_field') or '_PARTITIONTIME'} ({t.get('partition_type') or 'DAY'})"
    if t.get("clustering"):
        out["clustering"] = t["clustering"]
    out["cantidad_de_columnas"] = len(t.get("columns") or [])
    out["columnas"] = [
        {k: v for k, v in (("nombre", c["name"]), ("tipo", c.get("type")), ("descripcion", c.get("description"))) if v}
        for c in t.get("columns") or []
    ]
    return out


service = KnowledgeService()
