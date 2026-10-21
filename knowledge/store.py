"""Base SQLite del conocimiento (data/knowledge.db), con búsqueda FTS5.

Tres capas que nunca se mezclan:
- `catalog`: metadatos de BigQuery (autor `sistema`). Se resincroniza y nadie lo edita.
- `entries`: conocimiento de las personas, compartido o privado, con autor y estado.
- `events`: qué pasó con cada entrada (alta, aprobación, rechazo, deprecación).

Nada se borra ni se edita en el lugar: el contenido de una entrada es inmutable.
Una corrección es una entrada nueva con `supersedes_id`; lo único que cambia de
una entrada existente es su estado (y quién lo cambió y cuándo).

Este módulo solo guarda y lee. Las reglas (quién ve qué, quién aprueba, la
validación del SQL) están en `service.py`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .. import config

TARGET_TYPES = ("table", "column", "metric", "term", "join")
KINDS = (
    "descripcion",
    "definicion_metrica",
    "regla_filtro",
    "advertencia",
    "join",
    "sinonimo",
    "consulta_ejemplo",
)
VISIBILITIES = ("private", "shared")
STATUSES = ("proposed", "approved", "rejected", "deprecated")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id TEXT PRIMARY KEY,
  target_type TEXT NOT NULL,
  target_ref TEXT NOT NULL,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  sql TEXT,
  sources TEXT NOT NULL DEFAULT '[]',
  stale_after TEXT,
  author_id TEXT NOT NULL,
  proposed_via TEXT NOT NULL,
  source_session_id TEXT,
  visibility TEXT NOT NULL,
  status TEXT NOT NULL,
  reviewed_by TEXT,
  reviewed_at TEXT,
  review_note TEXT,
  supersedes_id TEXT REFERENCES entries(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_entries_target ON entries(target_ref COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_entries_status ON entries(status, visibility);
CREATE INDEX IF NOT EXISTS ix_entries_supersedes ON entries(supersedes_id);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id TEXT NOT NULL REFERENCES entries(id),
  action TEXT NOT NULL,
  actor TEXT NOT NULL,
  at TEXT NOT NULL,
  note TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_entry ON events(entry_id);

CREATE TABLE IF NOT EXISTS catalog (
  ref TEXT PRIMARY KEY,
  dataset TEXT NOT NULL,
  name TEXT NOT NULL,
  table_type TEXT,
  description TEXT,
  num_rows INTEGER,
  num_bytes INTEGER,
  partition_field TEXT,
  partition_type TEXT,
  clustering TEXT NOT NULL DEFAULT '[]',
  last_modified TEXT,
  columns TEXT NOT NULL DEFAULT '[]',
  synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
  entry_id UNINDEXED, target_ref, content, sql,
  tokenize = 'unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE IF NOT EXISTS catalog_fts USING fts5(
  ref UNINDEXED, name, description, columns,
  tokenize = 'unicode61 remove_diacritics 2'
);
"""

_ENTRY_JSON = ("sources",)
_CATALOG_JSON = ("clustering", "columns")


def now_iso() -> str:
    """Hora UTC con milisegundos: dos altas seguidas no empatan al ordenar."""
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return "kb_" + secrets.token_hex(5)


def clean_ref(ref: str) -> str:
    """`proyecto.dataset.tabla` sin backticks ni espacios; `proyecto:dataset.tabla` también vale."""
    ref = (ref or "").strip().strip("`").strip()
    return re.sub(r"^([\w-]+):", r"\1.", ref)


def fts_query(text: str) -> str | None:
    """Texto libre → consulta FTS5: cada palabra entre comillas, con prefijo, unidas por OR.

    Separa también por `_` y `.` (como el tokenizer), así `UK_SALES_DAILY` encuentra la tabla.
    """
    words = [w for w in re.findall(r"[^\W_]+", text or "") if len(w) > 1][:12]
    if not words:
        return None
    return " OR ".join(f'"{w}"*' if len(w) >= 3 else f'"{w}"' for w in words)


class StoreError(ValueError):
    """Pedido inválido sobre la base de conocimiento."""


class KnowledgeStore:
    def __init__(self, path: Path = config.KNOWLEDGE_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _read(self, sql: str, args: tuple | list = ()) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    # --- entradas ----------------------------------------------------------------

    @staticmethod
    def _entry(row: dict[str, Any]) -> dict[str, Any]:
        for k in _ENTRY_JSON:
            row[k] = json.loads(row.get(k) or "[]")
        return row

    def insert(self, entry: dict[str, Any], actor: str, note: str | None = None) -> dict[str, Any]:
        """Alta de una entrada (el contenido no se vuelve a tocar) y su evento."""
        e = {**entry}
        e.setdefault("id", new_id())
        e.setdefault("created_at", now_iso())
        e["updated_at"] = e["created_at"]
        e["sources"] = json.dumps(e.get("sources") or [], ensure_ascii=False)
        cols = (
            "id target_type target_ref kind content sql sources stale_after author_id proposed_via "
            "source_session_id visibility status reviewed_by reviewed_at review_note supersedes_id "
            "created_at updated_at"
        ).split()
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.execute(
                        f"INSERT INTO entries ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [e.get(c) for c in cols],
                    )
                    conn.execute(
                        "INSERT INTO entries_fts (entry_id, target_ref, content, sql) VALUES (?, ?, ?, ?)",
                        (e["id"], e["target_ref"], e["content"], e.get("sql") or ""),
                    )
                    conn.execute(
                        "INSERT INTO events (entry_id, action, actor, at, note) VALUES (?, ?, ?, ?, ?)",
                        (e["id"], "proposed" if e["status"] == "proposed" else "created", actor, e["created_at"], note),
                    )
            finally:
                conn.close()
        return self.get(e["id"])  # type: ignore[return-value]

    def set_status(
        self,
        entry_id: str,
        status: str,
        actor: str,
        *,
        action: str | None = None,
        note: str | None = None,
        review: bool = False,
    ) -> dict[str, Any]:
        """Cambia solo el estado. `review` registra además quién revisó y cuándo."""
        if status not in STATUSES:
            raise StoreError(f"Estado inválido: {status}")
        at = now_iso()
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    if review:
                        conn.execute(
                            "UPDATE entries SET status=?, reviewed_by=?, reviewed_at=?, review_note=?, updated_at=? WHERE id=?",
                            (status, actor, at, note, at, entry_id),
                        )
                    else:
                        conn.execute("UPDATE entries SET status=?, updated_at=? WHERE id=?", (status, at, entry_id))
                    conn.execute(
                        "INSERT INTO events (entry_id, action, actor, at, note) VALUES (?, ?, ?, ?, ?)",
                        (entry_id, action or status, actor, at, note),
                    )
            finally:
                conn.close()
        return self.get(entry_id)  # type: ignore[return-value]

    def get(self, entry_id: str) -> dict[str, Any] | None:
        rows = self._read("SELECT rowid AS seq, * FROM entries WHERE id = ?", (entry_id,))
        return self._entry(rows[0]) if rows else None

    def entries(
        self,
        *,
        target: str | None = None,
        include_children: bool = True,
        statuses: tuple[str, ...] | None = None,
        visibility: str | None = None,
        ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Entradas filtradas. `target` acepta el nombre completo o solo el de la tabla;
        con `include_children`, una tabla trae también las entradas de sus columnas."""
        where, args = [], []
        if target:
            ref = clean_ref(target)
            conds = ["target_ref = ? COLLATE NOCASE"]
            args.append(ref)
            if include_children:
                conds.append("target_ref LIKE ? ESCAPE '\\'")
                args.append(_like(ref) + ".%")
            if ref.count(".") < 2:  # nombre corto: tabla o dataset.tabla
                conds.append("target_ref LIKE ? ESCAPE '\\'")
                args.append("%." + _like(ref))
                if include_children:
                    conds.append("target_ref LIKE ? ESCAPE '\\'")
                    args.append("%." + _like(ref) + ".%")
            where.append("(" + " OR ".join(conds) + ")")
        if statuses:
            where.append(f"status IN ({', '.join('?' * len(statuses))})")
            args += list(statuses)
        if visibility:
            where.append("visibility = ?")
            args.append(visibility)
        if ids is not None:
            if not ids:
                return []
            where.append(f"id IN ({', '.join('?' * len(ids))})")
            args += ids
        sql = "SELECT rowid AS seq, * FROM entries" + (" WHERE " + " AND ".join(where) if where else "")
        return [self._entry(r) for r in self._read(sql + " ORDER BY created_at DESC, rowid DESC", args)]

    def chain(self, entry_id: str) -> list[dict[str, Any]]:
        """Historia de una entrada: las que reemplazó y las que la reemplazan, en orden."""
        seen: dict[str, dict[str, Any]] = {}
        todo = [entry_id]
        while todo:
            eid = todo.pop()
            if eid in seen:
                continue
            e = self.get(eid)
            if not e:
                continue
            seen[eid] = e
            if e.get("supersedes_id"):
                todo.append(e["supersedes_id"])
            todo += [r["id"] for r in self._read("SELECT id FROM entries WHERE supersedes_id = ?", (eid,))]
        return sorted(seen.values(), key=lambda e: (e["created_at"], e["seq"]))

    def events(self, entry_ids: list[str] | None = None) -> list[dict[str, Any]]:
        if entry_ids is not None and not entry_ids:
            return []
        if entry_ids is None:
            return self._read("SELECT * FROM events ORDER BY at, id")
        marks = ", ".join("?" * len(entry_ids))
        return self._read(f"SELECT * FROM events WHERE entry_id IN ({marks}) ORDER BY at, id", entry_ids)

    def search_entries(self, text: str, limit: int = 50) -> list[tuple[str, float]]:
        """Ids de entradas que coinciden con el texto, mejor puntaje primero."""
        q = fts_query(text)
        if not q:
            return []
        rows = self._read(
            "SELECT entry_id, bm25(entries_fts) AS score FROM entries_fts WHERE entries_fts MATCH ? ORDER BY score LIMIT ?",
            (q, limit),
        )
        return [(r["entry_id"], r["score"]) for r in rows]

    # --- catálogo ----------------------------------------------------------------

    @staticmethod
    def _table(row: dict[str, Any]) -> dict[str, Any]:
        for k in _CATALOG_JSON:
            row[k] = json.loads(row.get(k) or "[]")
        return row

    def replace_catalog(
        self, tables: list[dict[str, Any]], datasets: list[str], keep: set[str] | None = None
    ) -> dict[str, int]:
        """Reemplaza el catálogo de esos datasets por lo leído de BigQuery.

        `keep`: tablas que no se pudieron leer esta vez; se conservan como estaban."""
        synced = now_iso()
        cols = (
            "ref dataset name table_type description num_rows num_bytes partition_field "
            "partition_type clustering last_modified columns synced_at"
        ).split()
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    before = {
                        r[0] for r in conn.execute(
                            f"SELECT ref FROM catalog WHERE dataset IN ({', '.join('?' * len(datasets))})", datasets
                        )
                    } if datasets else set()
                    keep_refs = keep or set()
                    keep = {t["ref"] for t in tables}
                    gone = before - keep - keep_refs
                    for ref in gone:
                        conn.execute("DELETE FROM catalog WHERE ref = ?", (ref,))
                        conn.execute("DELETE FROM catalog_fts WHERE ref = ?", (ref,))
                    for t in tables:
                        row = {**t, "synced_at": synced}
                        for k in _CATALOG_JSON:
                            row[k] = json.dumps(row.get(k) or [], ensure_ascii=False)
                        conn.execute(
                            f"INSERT OR REPLACE INTO catalog ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            [row.get(c) for c in cols],
                        )
                        conn.execute("DELETE FROM catalog_fts WHERE ref = ?", (t["ref"],))
                        col_text = " ".join(
                            f"{c.get('name', '')} {c.get('description') or ''}" for c in t.get("columns") or []
                        )
                        conn.execute(
                            "INSERT INTO catalog_fts (ref, name, description, columns) VALUES (?, ?, ?, ?)",
                            (t["ref"], t["name"], t.get("description") or "", col_text),
                        )
            finally:
                conn.close()
        return {"tables": len(tables), "removed": len(gone)}

    def catalog(self, ref: str | None = None) -> list[dict[str, Any]]:
        if ref is None:
            return [self._table(r) for r in self._read("SELECT * FROM catalog ORDER BY dataset, name")]
        ref = clean_ref(ref)
        rows = self._read("SELECT * FROM catalog WHERE ref = ? COLLATE NOCASE", (ref,))
        if not rows and ref.count(".") < 2:
            rows = self._read(
                "SELECT * FROM catalog WHERE ref LIKE ? ESCAPE '\\' OR name = ? COLLATE NOCASE",
                ("%." + _like(ref), ref),
            )
        return [self._table(r) for r in rows]

    def search_catalog(self, text: str, limit: int = 10) -> list[tuple[str, float]]:
        q = fts_query(text)
        if not q:
            return []
        rows = self._read(
            "SELECT ref, bm25(catalog_fts) AS score FROM catalog_fts WHERE catalog_fts MATCH ? ORDER BY score LIMIT ?",
            (q, limit),
        )
        return [(r["ref"], r["score"]) for r in rows]

    # --- metadatos -----------------------------------------------------------------

    def get_meta(self, key: str) -> Any:
        rows = self._read("SELECT value FROM meta WHERE key = ?", (key,))
        return json.loads(rows[0]["value"]) if rows and rows[0]["value"] is not None else None

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        (key, json.dumps(value, ensure_ascii=False, default=str)),
                    )
            finally:
                conn.close()


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
