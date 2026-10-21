"""Reglas y operaciones sobre artifacts, compartidas por las tools del agente y el servidor.

Las tres formas de que un número llegue a la página (ver docs/):

    live   consulta en vivo: la página la repite con caché y TTL; cero tokens.
    saved  resultados guardados: el agente corre la consulta y guarda la foto.
    file   archivo adjunto (CSV, TSV, JSON): se sube una vez.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
import time
from typing import Any

from .. import config
from ..warehouse import sql_guard
from ..warehouse.mcp_client import BigQueryMcpClient, WarehouseError, client as default_client
from . import validator
from .store import ArtifactStore, StoreError, doc_kind, now_iso, store as default_store

PARAM_TYPES = {"text", "select", "multi", "date", "number", "boolean"}
MODES = {"live", "saved", "file"}
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_PLATFORM_FIELDS = ("columns", "params", "tested_at", "row_count", "bytes_processed")


class ArtifactError(ValueError):
    """Error que se le devuelve tal cual al agente o al visor."""


def _parse_json(content: str, path: str) -> Any:
    try:
        return json.loads(content)
    except ValueError as e:
        raise ArtifactError(f"{path} debe ser JSON válido: {e}") from e


class ArtifactService:
    def __init__(self, store: ArtifactStore = default_store, warehouse: BigQueryMcpClient = default_client):
        self.store = store
        self.warehouse = warehouse
        self._locks: dict[str, asyncio.Lock] = {}

    # --- lectura --------------------------------------------------------------

    def require(self, artifact_id: str) -> dict[str, Any]:
        try:
            meta = self.store.read_json(artifact_id, "dash/meta")
        except StoreError as e:
            raise ArtifactError(str(e)) from e
        if not meta:
            raise ArtifactError(f"No existe el artifact {artifact_id}.")
        return meta

    def require_editor(self, artifact_id: str, user_id: str) -> dict[str, Any]:
        meta = self.require(artifact_id)
        if meta.get("owner") != user_id:
            raise ArtifactError("Solo quien creó el artifact puede editarlo.")
        return meta

    def param_specs(self, artifact_id: str) -> dict[str, dict[str, Any]]:
        doc = self.store.read_json(artifact_id, "dash/params") or {}
        return {p["name"]: p for p in doc.get("params", [])}

    def describe(self, artifact_id: str, include_page: bool = False) -> dict[str, Any]:
        meta = self.require(artifact_id)
        datasets = self.store.datasets(artifact_id)
        out = {
            "meta": meta,
            "params": (self.store.read_json(artifact_id, "dash/params") or {}).get("params", []),
            "datasets": datasets,
            "docs": self.store.list_docs(artifact_id),
            "url": f"/a/{artifact_id}",
        }
        if include_page:
            out["page"] = self.store.read_doc(artifact_id, "files/index.html") or ""
        return out

    # --- escritura (reglas forzadas) ------------------------------------------

    def create(self, *, kind: str, title: str, description: str, owner: str) -> dict[str, Any]:
        if kind not in ("dashboard", "analysis"):
            raise ArtifactError("kind debe ser 'dashboard' o 'analysis'.")
        if not title.strip():
            raise ArtifactError("El título es obligatorio.")
        return self.store.create(kind=kind, title=title.strip(), description=description.strip(), owner=owner)

    def write(self, artifact_id: str, path: str, content: str, user_id: str) -> dict[str, Any]:
        meta = self.require_editor(artifact_id, user_id)
        try:
            kind = doc_kind(path)
        except StoreError as e:
            raise ArtifactError(str(e)) from e
        warnings: list[str] = []
        extra: dict[str, Any] = {}

        if kind == "dash/meta":
            data = _parse_json(content, path)
            if not isinstance(data, dict):
                raise ArtifactError("dash/meta debe ser un objeto.")
            for key in ("title", "description"):
                if key in data:
                    meta[key] = str(data[key])
            if data.get("kind") in ("dashboard", "analysis"):
                meta["kind"] = data["kind"]
            content = json.dumps(meta, ensure_ascii=False, indent=1)
        elif kind == "dash/params":
            content = json.dumps({"params": self._validate_params(_parse_json(content, path))}, ensure_ascii=False, indent=1)
        elif kind == "datasets":
            ds_id = path.split("/", 1)[1]
            if not content.strip():
                self.store.delete_doc(artifact_id, path)
                return {"ok": True, "deleted": path}
            dataset, note = self._validate_dataset(artifact_id, ds_id, _parse_json(content, path))
            content = json.dumps(dataset, ensure_ascii=False, indent=1)
            if note:
                extra["next_step"] = note
        else:
            result = validator.check_page(content, set(self.store.datasets(artifact_id)))
            if result["errors"]:
                raise ArtifactError("Página rechazada: " + " ".join(result["errors"]))
            warnings = result["warnings"]
            extra["checklist"] = validator.CHECKLIST

        try:
            self.store.write_doc(artifact_id, path, content)
        except StoreError as e:
            raise ArtifactError(str(e)) from e
        return {"ok": True, "path": path, "warnings": warnings, **extra}

    def _validate_params(self, data: Any) -> list[dict[str, Any]]:
        items = data.get("params") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ArtifactError('dash/params debe ser {"params": [...]}.')
        seen, out = set(), []
        for p in items:
            if not isinstance(p, dict) or not _NAME_RE.match(str(p.get("name", ""))):
                raise ArtifactError(f"Parámetro inválido {p!r}: name en minúsculas, dígitos y _.")
            typ = p.get("type", "text")
            if typ not in PARAM_TYPES:
                raise ArtifactError(f"Tipo de parámetro inválido '{typ}'. Válidos: {sorted(PARAM_TYPES)}.")
            if p["name"] in seen:
                raise ArtifactError(f"Parámetro duplicado: {p['name']}.")
            seen.add(p["name"])
            if p.get("default") is not None:
                sql_guard.literal(p["default"], typ)  # valida el formato
            opts = p.get("options")
            if opts is not None and not (
                isinstance(opts, list) or (isinstance(opts, dict) and {"dataset", "field"} <= opts.keys())
            ):
                raise ArtifactError('options debe ser una lista o {"dataset": id, "field": columna}.')
            out.append({k: p[k] for k in ("name", "label", "type", "default", "options", "help") if k in p} | {"type": typ})
        return out

    def _validate_dataset(self, artifact_id: str, ds_id: str, data: Any) -> tuple[dict[str, Any], str | None]:
        if not isinstance(data, dict):
            raise ArtifactError("El dataset debe ser un objeto JSON.")
        mode = data.get("mode", "live")
        if mode not in MODES:
            raise ArtifactError(f"mode inválido '{mode}'. Válidos: live, saved, file.")
        if mode == "file":
            raise ArtifactError("Los datasets de archivo se crean subiendo el archivo desde la interfaz.")
        connector = data.get("connector", "bigquery")
        tool = data.get("tool", "execute_sql_readonly")
        if tool not in config.CONNECTOR_TOOLS.get(connector, set()):
            raise ArtifactError(
                f"Herramienta '{tool}' no permitida para el conector '{connector}'."
                f" Permitidas: {sorted(config.CONNECTOR_TOOLS.get(connector, []))}."
            )
        sql = (data.get("sql") or "").strip()
        try:
            params = sql_guard.check_query(sql)
        except sql_guard.SqlGuardError as e:
            raise ArtifactError(f"SQL rechazado: {e}") from e
        if mode == "saved" and params:
            raise ArtifactError("Un dataset 'saved' es una foto: no usa parámetros. Filtrá en la página o usá 'live'.")
        declared = self.param_specs(artifact_id)
        undeclared = [p for p in params if p not in declared]
        if undeclared:
            raise ArtifactError(f"Parámetros sin declarar en dash/params: {', '.join(undeclared)}. Escribí dash/params primero.")
        if not data.get("title"):
            raise ArtifactError("El dataset necesita un title legible (se muestra en el panel Fuentes).")
        ttl = max(60, int(data.get("ttl_seconds") or config.DEFAULT_TTL_SECONDS))
        dataset = {
            "id": ds_id,
            "title": str(data["title"]),
            "description": str(data.get("description", "")),
            "mode": mode,
            "connector": connector,
            "tool": tool,
            "sql": sql,
            "params": params,
            "ttl_seconds": ttl,
        }
        previous = self.store.datasets(artifact_id).get(ds_id)
        if previous and previous.get("sql") == sql:
            dataset |= {k: previous[k] for k in _PLATFORM_FIELDS if k in previous and k != "params"}
            return dataset, None
        step = "artifact_save_results" if mode == "saved" else "artifact_test_dataset"
        return dataset, f"Consulta nueva o cambiada: corré {step} para probarla y registrar sus columnas."

    # --- ejecución -------------------------------------------------------------

    def _dataset(self, artifact_id: str, ds_id: str) -> dict[str, Any]:
        self.require(artifact_id)
        ds = self.store.datasets(artifact_id).get(ds_id)
        if not ds:
            raise ArtifactError(f"No existe el dataset '{ds_id}' en {artifact_id}.")
        return ds

    def _render_sql(self, artifact_id: str, ds: dict[str, Any], values: dict[str, Any] | None) -> tuple[str, dict]:
        specs = self.param_specs(artifact_id)
        used = {p: (values or {}).get(p, specs.get(p, {}).get("default")) for p in ds.get("params", [])}
        try:
            sql_guard.check_query(ds["sql"])  # se revalida en cada corrida
            return sql_guard.render(ds["sql"], specs, used), used
        except sql_guard.SqlGuardError as e:
            raise ArtifactError(str(e)) from e

    async def _execute(self, sql: str, warehouse: BigQueryMcpClient | None = None) -> dict[str, Any]:
        warehouse = warehouse or self.warehouse
        scanned = await warehouse.dry_run(sql)
        if scanned > config.MAX_BYTES_SCANNED:
            raise ArtifactError(
                f"La consulta escanearía {scanned / 1024**3:.1f} GiB (límite"
                f" {config.MAX_BYTES_SCANNED / 1024**3:.0f} GiB)."
            )
        result = await warehouse.run_query(sql, labels={"origin": "artifact"})
        return {
            "columns": result.columns,
            "rows": result.rows,
            "total_rows": result.total_rows,
            "truncated": result.truncated,
            "bytes_processed": result.bytes_processed,
            "elapsed_ms": result.elapsed_ms,
            "cached_at": now_iso(),
            "cached_ts": time.time(),
        }

    async def test_dataset(self, artifact_id: str, ds_id: str, values: dict[str, Any] | None, user_id: str) -> dict[str, Any]:
        self.require_editor(artifact_id, user_id)
        ds = self._dataset(artifact_id, ds_id)
        sql, used = self._render_sql(artifact_id, ds, values)
        try:
            data = await self._execute(sql)
        except WarehouseError as e:
            raise ArtifactError(f"La consulta falló en BigQuery: {e}") from e
        if ds["mode"] == "live":
            self.store.write_data(artifact_id, "cache", self._cache_key(ds_id, sql), data)
        else:
            self.store.write_data(artifact_id, "results", ds_id, data)
        ds |= {
            "columns": data["columns"],
            "tested_at": data["cached_at"],
            "row_count": data["total_rows"],
            "bytes_processed": data["bytes_processed"],
        }
        self.store.write_doc(artifact_id, f"datasets/{ds_id}", json.dumps(ds, ensure_ascii=False, indent=1))
        return {
            "ok": True,
            "dataset": ds_id,
            "params_used": used,
            "columns": data["columns"],
            "total_rows": data["total_rows"],
            "truncated": data["truncated"],
            "bytes_processed": data["bytes_processed"],
            "sample_rows": data["rows"][:10],
        }

    @staticmethod
    def _cache_key(ds_id: str, sql: str) -> str:
        return f"{ds_id}__{hashlib.sha1(sql.encode()).hexdigest()[:16]}"

    async def get_data(
        self, artifact_id: str, ds_id: str, values: dict[str, Any] | None, cache: str = "prefer",
        warehouse: BigQueryMcpClient | None = None,
    ) -> dict[str, Any]:
        """Lo que pide el visor: caché primero y refresco cuando queda viejo."""
        ds = self._dataset(artifact_id, ds_id)
        if ds["mode"] in ("saved", "file"):
            data = self.store.read_data(artifact_id, "results", ds_id)
            if not data:
                raise ArtifactError(f"El dataset '{ds_id}' no tiene resultados guardados todavía.")
            return {**data, "dataset": ds_id, "mode": ds["mode"], "stale": False}
        sql, used = self._render_sql(artifact_id, ds, values)
        key = self._cache_key(ds_id, sql)
        cached = self.store.read_data(artifact_id, "cache", key)
        age = time.time() - cached.get("cached_ts", 0) if cached else None
        stale = cached is None or age > ds.get("ttl_seconds", config.DEFAULT_TTL_SECONDS)
        if cached and (cache == "prefer" or (cache == "auto" and not stale)):
            return {**cached, "dataset": ds_id, "mode": "live", "stale": stale, "params_used": used}
        lock = self._locks.setdefault(f"{artifact_id}/{key}", asyncio.Lock())
        async with lock:
            fresh = self.store.read_data(artifact_id, "cache", key)
            # Si otra pestaña refrescó mientras esperábamos el lock, se reusa.
            if fresh and fresh.get("cached_ts", 0) > (cached or {}).get("cached_ts", 0):
                data = fresh
            else:
                try:
                    data = await self._execute(sql, warehouse)
                except WarehouseError as e:
                    raise ArtifactError(str(e)) from e
                self.store.write_data(artifact_id, "cache", key, data)
        return {**data, "dataset": ds_id, "mode": "live", "stale": False, "params_used": used}

    # --- archivos adjuntos ----------------------------------------------------

    def attach_file(self, artifact_id: str, filename: str, raw: bytes, user_id: str) -> dict[str, Any]:
        self.require_editor(artifact_id, user_id)
        if len(raw) > config.MAX_RESULT_BYTES:
            raise ArtifactError("El archivo supera 8 MB.")
        stem = re.sub(r"[^a-z0-9_]+", "_", filename.rsplit(".", 1)[0].lower()).strip("_") or "archivo"
        ds_id = ("f_" + stem)[:60]
        text = raw.decode("utf-8-sig")
        if filename.lower().endswith(".json"):
            rows = json.loads(text)
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ArtifactError("El JSON debe ser una lista de objetos.")
        else:
            dialect = "excel-tab" if filename.lower().endswith(".tsv") or "\t" in text.split("\n", 1)[0] else "excel"
            rows = [{k: _infer(v) for k, v in r.items()} for r in csv.DictReader(io.StringIO(text), dialect=dialect)]
        columns = [{"name": k, "type": _col_type([r.get(k) for r in rows])} for k in (rows[0].keys() if rows else [])]
        data = {"columns": columns, "rows": rows, "total_rows": len(rows), "truncated": False, "cached_at": now_iso(), "cached_ts": time.time()}
        self.store.write_data(artifact_id, "results", ds_id, data)
        dataset = {
            "id": ds_id, "title": filename, "description": "Archivo adjunto", "mode": "file",
            "connector": "file", "tool": None, "sql": None, "params": [], "columns": columns,
            "row_count": len(rows), "tested_at": data["cached_at"],
        }
        self.store.write_doc(artifact_id, f"datasets/{ds_id}", json.dumps(dataset, ensure_ascii=False, indent=1))
        return {"ok": True, "dataset": ds_id, "columns": columns, "total_rows": len(rows)}

    # --- corridas -------------------------------------------------------------

    def record_run(self, artifact_id: str, record: dict[str, Any], viewer: str) -> None:
        self.require(artifact_id)
        clean = {
            "at": now_iso(),
            "viewer": viewer,
            "datasets_loaded": record.get("datasets_loaded", []),
            "source_failures": record.get("source_failures", [])[:20],
            "mismatches": record.get("mismatches", [])[:50],
            "errors": record.get("errors", [])[:20],
            "removed": record.get("removed", [])[:20],
            "unmarked_numbers": record.get("unmarked_numbers", 0),
        }
        self.store.append_run(artifact_id, json.loads(json.dumps(clean, default=str)[:200_000]))

    def runs_summary(self, artifact_id: str, last: int = 5) -> dict[str, Any]:
        self.require(artifact_id)
        runs = self.store.read_runs(artifact_id)
        recent = runs[-last:]
        issues = sum(len(r["source_failures"]) + len(r["mismatches"]) + len(r["errors"]) for r in recent)
        return {
            "total_runs": len(runs),
            "recent": recent,
            "status": "sin corridas todavía" if not runs else ("con problemas" if issues else "ok"),
        }


def _infer(value: str | None) -> Any:
    if value is None or value == "":
        return None
    v = value.strip()
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d*(e-?\d+)?", v, re.I):
        return float(v)
    return v


def _col_type(values: list[Any]) -> str:
    present = [v for v in values if v is not None]
    if present and all(isinstance(v, int) for v in present):
        return "INTEGER"
    if present and all(isinstance(v, (int, float)) for v in present):
        return "FLOAT"
    return "STRING"


service = ArtifactService()
