"""Dashboards en vivo, contrato 2: almacén, reglas forzadas, filtros y fuentes.

El almacén es una base documental (rutas `colección/doc_id`) más blobs:

    dash/meta                {title}
    dash/params              {nombre: valor por defecto}
    datasets/<id>            {title, description?, source, updated?, request?, lastAttempt?,
                              schedule?, staleAfterHours?}
    files/<nombre>           {text}: index.html y archivos que carga
    data/users/<uid>/runs    documento de corridas que escribe la página de cada lector
    /_blob/<id>              archivos adjuntos y resultados guardados (CSV, TSV, JSON)

`source` es una llamada a herramienta MCP serializada ({kind: "query", connector, tool,
args}) o un archivo ({kind: "file", url, name, format?}). Todo lo que se fuerza vive acá;
las convenciones están en la skill `dashboard`.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import config
from ..warehouse import sql_guard
from ..warehouse.mcp_client import BigQueryMcpClient, WarehouseError, client as default_client, rows_from_payload
from . import validator

CONTRACT = 2
MAX_DOC_BYTES = 256 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_BATCH_WRITES = 50
MAX_BATCH_BYTES = 1024 * 1024

# Registro de conectores: herramienta permitida, argumento que lleva el SQL, auxiliares y prueba.
CONNECTORS: dict[str, dict[str, Any]] = {
    "Google Cloud BigQuery": {
        "tool": "execute_sql_readonly",
        "sql_arg": "query",
        "aux": {"get_query_results"},
        "test": "list_dataset_ids",
    },
}

_DATASET_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FILE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_ARTIFACT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
_PARAM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BLOB_URL = re.compile(r"^/_blob/([a-f0-9]{16,40})$")


class DashError(ValueError):
    """Una escritura o una carga que el tipo rechaza. `status` va al visor."""

    def __init__(self, message: str, status: str = "error"):
        super().__init__(message)
        self.status = status


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slug(text: str) -> str:
    text = text.lower()
    for a, b in zip("áéíóúüñ", "aeiouun"):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or "dashboard"


def _size(data: Any) -> int:
    return len(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))


# ---------------------------------------------------------------- almacén


class DashStore:
    def __init__(self, root: Path = config.ARTIFACTS_DIR):
        self.root = Path(root)
        self._lock = threading.RLock()

    def dir(self, aid: str) -> Path:
        if not _ARTIFACT_ID.match(aid or ""):
            raise DashError(f"Id de dashboard inválido: {aid!r}", "missing")
        return self.root / aid

    def is_v2(self, aid: str) -> bool:
        try:
            return (self.dir(aid) / "_platform.json").exists()
        except DashError:
            return False

    def _write(self, file: Path, data: str) -> None:
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_name(file.name + f".{secrets.token_hex(4)}.tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, file)

    # plataforma (no es parte del almacén que ve la página)
    def platform(self, aid: str) -> dict[str, Any]:
        f = self.dir(aid) / "_platform.json"
        if not f.exists():
            raise DashError(f"No existe el dashboard {aid}.", "missing")
        return json.loads(f.read_text(encoding="utf-8"))

    def touch(self, aid: str) -> None:
        p = self.platform(aid)
        p["updated_at"] = now_iso()
        self._write(self.dir(aid) / "_platform.json", json.dumps(p, ensure_ascii=False, indent=1))

    def set_project(self, aid: str, project_id: str | None) -> dict[str, Any]:
        """Mueve el dashboard a un proyecto (None = personal de quien lo creó)."""
        with self._lock:
            p = self.platform(aid)
            if project_id:
                p["project_id"] = project_id
            else:
                p.pop("project_id", None)
            p["updated_at"] = now_iso()
            self._write(self.dir(aid) / "_platform.json", json.dumps(p, ensure_ascii=False, indent=1))
        return p

    def create(self, title: str, owner: str) -> str:
        aid = f"{_slug(title)}-{secrets.token_hex(3)}"
        with self._lock:
            platform = {"id": aid, "owner": owner, "contract": CONTRACT, "created_at": now_iso(), "updated_at": now_iso()}
            self._write(self.dir(aid) / "_platform.json", json.dumps(platform, ensure_ascii=False, indent=1))
            self._write(self._doc_file(aid, "dash", "meta"), json.dumps({"title": title}, ensure_ascii=False))
        return aid

    def list(self, owner: str | None = None) -> list[dict[str, Any]]:
        out = []
        if not self.root.exists():
            return out
        for d in self.root.iterdir():
            pf = d / "_platform.json"
            if not pf.exists():
                continue
            p = json.loads(pf.read_text(encoding="utf-8"))
            if owner and p.get("owner") != owner:
                continue
            meta = self.get(p["id"], "dash", "meta") or {}
            out.append({**p, "title": meta.get("title", p["id"])})
        return out

    # documentos
    def _doc_file(self, aid: str, collection: str, doc_id: str) -> Path:
        parts = [p for p in collection.split("/") if p]
        # Los ids de usuario son mails (data/users/ana@empresa.com/runs): se admiten @ y +.
        # "." y ".." nunca, para que una ruta no salga del dashboard.
        if not parts or any(not re.match(r"^[A-Za-z0-9_.@+-]+$", p) or p in (".", "..") for p in parts + [doc_id]):
            raise DashError(f"Ruta inválida: {collection}/{doc_id}")
        return self.dir(aid).joinpath("db", *parts, f"{doc_id}.json")

    def get(self, aid: str, collection: str, doc_id: str) -> Any:
        f = self._doc_file(aid, collection, doc_id)
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def list_docs(self, aid: str, collection: str) -> dict[str, Any]:
        folder = self._doc_file(aid, collection, "x").parent  # misma validación de la ruta que un documento
        if not folder.exists():
            return {}
        return {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(folder.glob("*.json"))}

    def _keep_history(self, aid: str, collection: str, doc_id: str) -> None:
        if collection not in ("datasets", "files"):
            return
        f = self._doc_file(aid, collection, doc_id)
        if f.exists():
            stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S%f")
            h = self.dir(aid) / "history" / collection / doc_id / f"{stamp}.json"
            h.parent.mkdir(parents=True, exist_ok=True)
            h.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")

    def put(self, aid: str, collection: str, doc_id: str, data: Any) -> None:
        with self._lock:
            self._keep_history(aid, collection, doc_id)
            self._write(self._doc_file(aid, collection, doc_id), json.dumps(data, ensure_ascii=False, indent=1))

    def delete(self, aid: str, collection: str, doc_id: str) -> bool:
        with self._lock:
            f = self._doc_file(aid, collection, doc_id)
            if not f.exists():
                return False
            self._keep_history(aid, collection, doc_id)
            f.unlink()
            return True

    def versions(self, aid: str, collection: str, doc_id: str) -> list[dict[str, Any]]:
        folder = self.dir(aid) / "history" / collection / doc_id
        if not folder.exists():
            return []
        out = []
        for f in sorted(folder.glob("*.json"), reverse=True)[:30]:
            at = dt.datetime.strptime(f.stem, "%Y%m%dT%H%M%S%f").isoformat(timespec="seconds")
            out.append({"version": f.stem, "at": at, "data": json.loads(f.read_text(encoding="utf-8"))})
        return out

    # blobs
    def put_asset(self, aid: str, name: str, raw: bytes) -> dict[str, str]:
        if len(raw) > MAX_FILE_BYTES:
            raise DashError(f"El archivo pesa {len(raw) / 1e6:.1f} MB y el máximo es 8 MB.")
        blob = secrets.token_hex(12)
        folder = self.dir(aid) / "assets"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / blob).write_bytes(raw)
        (folder / f"{blob}.name").write_text(name, encoding="utf-8")
        return {"url": f"/_blob/{blob}", "name": name}

    def get_asset(self, aid: str, url: str) -> tuple[bytes, str]:
        m = _BLOB_URL.match(url or "")
        if not m:
            raise DashError("La url de un archivo es /_blob/<id>: una externa no se lee.")
        f = self.dir(aid) / "assets" / m.group(1)
        if not f.exists():
            raise DashError(f"No existe el archivo {url}.", "missing")
        name_f = f.with_name(f"{m.group(1)}.name")
        return f.read_bytes(), name_f.read_text(encoding="utf-8") if name_f.exists() else ""

    def delete_asset(self, aid: str, url: str) -> bool:
        m = _BLOB_URL.match(url or "")
        if not m:
            return False
        f = self.dir(aid) / "assets" / m.group(1)
        if f.exists():
            f.unlink()
            f.with_name(f"{m.group(1)}.name").unlink(missing_ok=True)
            return True
        return False

    def assets(self, aid: str) -> list[dict[str, Any]]:
        folder = self.dir(aid) / "assets"
        if not folder.exists():
            return []
        out = []
        for f in folder.iterdir():
            if f.suffix == ".name" or f.suffix == ".tmp":
                continue
            name_f = f.with_name(f"{f.name}.name")
            out.append({"url": f"/_blob/{f.name}", "name": name_f.read_text(encoding="utf-8") if name_f.exists() else "", "bytes": f.stat().st_size})
        return out

    # caché de consultas en vivo
    def cache_get(self, aid: str, key: str) -> dict[str, Any] | None:
        f = self.dir(aid) / "cache" / f"{key}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def cache_put(self, aid: str, key: str, payload: dict[str, Any]) -> None:
        self._write(self.dir(aid) / "cache" / f"{key}.json", json.dumps(payload, ensure_ascii=False, default=str))


# ---------------------------------------------------------------- lectura de archivos


def parse_file(raw: bytes, name: str, fmt: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """CSV/TSV (la primera fila nombra las columnas; las celdas llegan como texto) o JSON
    (un array de filas, o uno bajo rows/data/results)."""
    kind = (fmt or name.rsplit(".", 1)[-1]).lower()
    text = raw.decode("utf-8-sig")
    if kind == "json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = next((data[k] for k in ("rows", "data", "results") if isinstance(data.get(k), list)), None)
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise DashError("El JSON tiene que ser un array de objetos fila, o uno bajo rows, data o results.")
        cols: list[str] = []
        for r in data:
            cols += [k for k in r if k not in cols]
        return data, cols
    if kind in ("csv", "tsv"):
        reader = csv.DictReader(io.StringIO(text), dialect="excel-tab" if kind == "tsv" else "excel")
        rows = [dict(r) for r in reader]
        return rows, list(reader.fieldnames or [])
    raise DashError(f"Formato no soportado: {kind}. Se admiten csv, tsv y json.")


def _bytes_label(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


# ---------------------------------------------------------------- servicio


class DashboardService:
    def __init__(self, store: DashStore | None = None, warehouse: BigQueryMcpClient = default_client):
        self.store = store or DashStore()
        self.warehouse = warehouse
        self._locks: dict[str, asyncio.Lock] = {}
        # Permisos y credenciales: por defecto, solo el creador y la cuenta del servidor.
        # El servidor los reemplaza por los de proyectos (auth.access) y los de cada persona.
        self.can_view: Callable[[dict[str, Any], str], bool] = lambda p, user: p.get("owner") == user
        self.can_edit: Callable[[dict[str, Any], str], bool] = lambda p, user: p.get("owner") == user
        self.client_for: Callable[[str | None], BigQueryMcpClient] = lambda user: self.warehouse
        self.per_user_cache: Callable[[], bool] = lambda: False

    # --- acceso -----------------------------------------------------------------------
    def require(self, aid: str) -> dict[str, Any]:
        return self.store.platform(aid)

    def require_viewer(self, aid: str, user: str) -> dict[str, Any]:
        p = self.require(aid)
        if not self.can_view(p, user):
            # Mismo mensaje que si no existiera: no se confirma que el id es válido.
            raise DashError(f"No existe el dashboard {aid}.", "missing")
        return p

    def require_editor(self, aid: str, user: str) -> dict[str, Any]:
        p = self.require_viewer(aid, user)
        if not self.can_edit(p, user):
            raise DashError("Solo los editores pueden escribir en este dashboard.", "refused")
        return p

    def params_defaults(self, aid: str) -> dict[str, Any]:
        return self.store.get(aid, "dash", "params") or {}

    # --- validación de escrituras ------------------------------------------------------
    def _validate(self, aid: str, collection: str, doc_id: str, data: Any, pending: dict[str, Any]) -> list[str]:
        """Reglas forzadas por documento. Devuelve advertencias (convenciones)."""
        warnings: list[str] = []
        if _size(data) > MAX_DOC_BYTES:
            raise DashError(f"{collection}/{doc_id} pesa más de 256 KB: el almacén lo rechaza.")
        if collection.startswith("data/"):
            raise DashError("El documento de corridas lo escribe la página de cada lector; vos solo lo leés.")
        if collection == "dash" and doc_id == "meta":
            if not isinstance(data, dict) or not str(data.get("title", "")).strip():
                raise DashError("dash/meta es {title}: el nombre que le da la persona.")
        elif collection == "dash" and doc_id == "params":
            if not isinstance(data, dict):
                raise DashError("dash/params es {nombre: valor por defecto}.")
            for k, v in data.items():
                if not _PARAM_NAME.match(k):
                    raise DashError(f"Nombre de filtro inválido: {k!r} (letras, dígitos y _).")
                if sql_guard.clean_param_value(v) is None and v is not None:
                    raise DashError(f"Valor por defecto inválido para {k}: texto de una línea, número, booleano, null o lista de esos.")
        elif collection == "datasets":
            warnings += self._validate_dataset(aid, doc_id, data, pending)
        elif collection == "files":
            if not _FILE_NAME.match(doc_id) or "/" in doc_id:
                raise DashError(f"Nombre de archivo inválido: {doc_id!r} (sin carpetas).")
            if not isinstance(data, dict) or not isinstance(data.get("text"), str):
                raise DashError("Un archivo de la página es {text}.")
            text = data["text"]
            if len(text.encode("utf-8")) > MAX_DOC_BYTES:
                raise DashError(f"files/{doc_id} pesa más de 256 KB: se rechaza.")
            ids = set(self.store.list_docs(aid, "datasets")) | {k.split("/", 1)[1] for k in pending if k.startswith("datasets/")}
            if doc_id.endswith(".js"):
                result = validator.check_page(f"<script>{text}</script>", ids, contract=2)
            elif doc_id.endswith(".css"):
                result = validator.check_page(f"<style>{text}</style>", ids, contract=2)
            else:
                result = validator.check_page(text, ids, contract=2)
            if result["errors"]:
                raise DashError(f"files/{doc_id}: " + " ".join(result["errors"]))
            warnings += [f"files/{doc_id}: {w}" for w in result["warnings"]]
        else:
            raise DashError(f"Colección desconocida: {collection}. Son dash, datasets y files.")
        return warnings

    def _validate_call(self, call: dict[str, Any], what: str, allow_params: bool = True) -> list[str]:
        connector = call.get("connector")
        reg = CONNECTORS.get(connector or "")
        if not reg:
            raise DashError(f"{what}: conector desconocido {connector!r}. Disponibles: {', '.join(CONNECTORS)}.")
        if call.get("tool") != reg["tool"]:
            raise DashError(f"{what}: en {connector} la página acepta solo {reg['tool']}.")
        args = call.get("args")
        if not isinstance(args, dict):
            raise DashError(f"{what}: args es un objeto con los argumentos de la llamada.")
        sql = args.get(reg["sql_arg"])
        if not isinstance(sql, str):
            raise DashError(f"{what}: falta el SQL en args.{reg['sql_arg']}.")
        project = args.get("projectId")
        if project and project != config.WAREHOUSE_PROJECT:
            raise DashError(f"{what}: projectId tiene que ser {config.WAREHOUSE_PROJECT}.")
        try:
            params = sql_guard.check_sql_v2(sql)
        except sql_guard.SqlGuardError as e:
            raise DashError(f"{what}: {e}") from e
        if params and not allow_params:
            return ["Esta consulta cambia con un filtro, así que sus resultados guardados no se pueden mostrar: dejala en vivo."]
        return []

    def _validate_dataset(self, aid: str, ds_id: str, data: Any, pending: dict[str, Any]) -> list[str]:
        if not _DATASET_ID.match(ds_id):
            raise DashError(f"Id de dataset inválido: {ds_id!r} ([A-Za-z0-9_-], 1 a 64).")
        if not isinstance(data, dict):
            raise DashError("Un dataset es un objeto.")
        if not str(data.get("title", "")).strip():
            raise DashError(f"datasets/{ds_id}: falta title (2 a 5 palabras, ~30 caracteres).")
        warnings: list[str] = []
        title = str(data["title"])
        if len(title) > 40:
            warnings.append(f"datasets/{ds_id}: el title es largo ({len(title)} caracteres); unos 30, el detalle va en description.")
        source = data.get("source")
        if not isinstance(source, dict) or source.get("kind") not in ("query", "file"):
            raise DashError(f"datasets/{ds_id}: source es {{kind: 'query', connector, tool, args}} o {{kind: 'file', url, name}}.")
        if source["kind"] == "query":
            warnings += self._validate_call(source, f"datasets/{ds_id}")
        else:
            url = source.get("url", "")
            if not _BLOB_URL.match(url):
                raise DashError(f"datasets/{ds_id}: la url de un archivo es la que devolvió upload_asset (/_blob/<id>).")
            self.store.get_asset(aid, url)  # existe
            if not source.get("name"):
                raise DashError(f"datasets/{ds_id}: falta source.name, el nombre del archivo.")
            if source.get("format") not in (None, "csv", "tsv", "json"):
                raise DashError(f"datasets/{ds_id}: format es csv, tsv o json.")
            if not isinstance(data.get("updated"), dict) or not data["updated"].get("at"):
                warnings.append(f"datasets/{ds_id}: un archivo lleva updated: {{at, by}} en cada escritura; sin eso muestra una advertencia.")
            if isinstance(data.get("request"), dict):
                warnings += self._validate_call(data["request"], f"datasets/{ds_id}.request", allow_params=False)
        desc = str(data.get("description", ""))
        if source["kind"] == "query" and desc and not re.search(r"\b(Reads|Lee)\b", desc):
            warnings.append(f"datasets/{ds_id}: la description de una consulta termina con los datos que lee (\"Lee proyecto.tabla.\").")
        return warnings

    # --- escrituras (write_db) ---------------------------------------------------------
    def write(self, aid: str, user: str, writes: list[dict[str, Any]]) -> dict[str, Any]:
        self.require_editor(aid, user)
        if len(writes) > MAX_BATCH_WRITES:
            raise DashError(f"Un batch admite hasta {MAX_BATCH_WRITES} escrituras.")
        if _size(writes) > MAX_BATCH_BYTES:
            raise DashError("Un batch admite hasta 1 MB.")
        # Primero se resuelve el estado final de cada documento y se valida todo; recién después se escribe.
        final: dict[str, Any] = {}
        for w in writes:
            op = w.get("op") or w.get("db_op")
            collection, doc_id = w.get("collection", ""), str(w.get("doc_id", ""))
            key = f"{collection}/{doc_id}"
            current = final.get(key, self.store.get(aid, collection, doc_id))
            if op == "set":
                final[key] = w.get("data")
            elif op == "update":
                if current is None:
                    raise DashError(f"{key} no existe: usá set.")
                merged = dict(current)
                for k, v in (w.get("data") or {}).items():
                    if v is None:
                        merged.pop(k, None)
                    else:
                        merged[k] = v
                final[key] = merged
            elif op == "delete":
                final[key] = None
            else:
                raise DashError(f"Operación desconocida {op!r}: set, update o delete.")
        warnings: list[str] = []
        for key, data in final.items():
            collection, doc_id = key.rsplit("/", 1)
            if data is not None:
                warnings += self._validate(aid, collection, doc_id, data, final)
        if any(k.startswith("files/") for k in final):
            files = {**{f"files/{n}": d for n, d in self.store.list_docs(aid, "files").items()}, **{k: v for k, v in final.items() if k.startswith("files/")}}
            code = "\n".join((d or {}).get("text", "") for d in files.values())
            has_data = self.store.list_docs(aid, "datasets") or any(k.startswith("datasets/") and v for k, v in final.items())
            if code and has_data and "dash.onData" not in code:
                warnings.append("La página no dibuja en dash.onData: los números no se llenan desde los datos.")
            missing = sorted({m for m in re.findall(r"<(?:script[^>]*\bsrc|link[^>]*\bhref)=[\"']([^\"']+)[\"']", code)
                              if f"files/{m}" not in files or files[f"files/{m}"] is None})
            if missing:
                warnings.append(f"La página carga archivos que no existen: {', '.join(missing)}.")
        for key, data in final.items():
            collection, doc_id = key.rsplit("/", 1)
            if data is None:
                self.store.delete(aid, collection, doc_id)
            else:
                self.store.put(aid, collection, doc_id, data)
        self.store.touch(aid)
        return {"ok": True, "written": list(final), "warnings": warnings}

    def read(self, aid: str, collection: str, doc_id: str | None, user: str) -> Any:
        self.require_viewer(aid, user)
        collection = collection.replace("data/users/me", f"data/users/{user}")
        # Lo que escribe la página de cada lector (sus filtros, lo que vio) es suyo: nadie
        # más lo lee, aunque pueda ver el dashboard.
        if collection.strip("/").split("/")[0] == "data" and collection.strip("/") != f"data/users/{user}":
            raise DashError("Solo podés leer tu propio documento de corridas: data/users/me.", "refused")
        if doc_id:
            return self.store.get(aid, collection, doc_id)
        return self.store.list_docs(aid, collection)

    # --- ejecución de fuentes ------------------------------------------------------------
    def _values(self, aid: str, values: dict[str, Any] | None) -> dict[str, Any]:
        merged = dict(self.params_defaults(aid))
        for k, v in (values or {}).items():
            if _PARAM_NAME.match(k):
                merged[k] = sql_guard.clean_param_value(v)
        return merged

    async def run_call(self, call: dict[str, Any], values: dict[str, Any], raw: bool = False, user: str | None = None) -> dict[str, Any]:
        """Corre una llamada serializada con los filtros completados, con las credenciales de `user`."""
        warehouse = self.client_for(user)
        reg = CONNECTORS.get(call.get("connector") or "")
        if not reg or call.get("tool") != reg["tool"]:
            raise DashError("Herramienta no permitida para este conector.", "refused")
        try:
            args = sql_guard.render_args_v2(call.get("args") or {}, values, reg["sql_arg"])
            sql_guard.check_sql_v2(args[reg["sql_arg"]])
        except sql_guard.SqlGuardError as e:
            raise DashError(str(e), "refused") from e
        sql = args[reg["sql_arg"]]
        started = time.monotonic()
        try:
            scanned = await warehouse.dry_run(sql)
            if scanned > config.MAX_BYTES_SCANNED:
                raise DashError(
                    f"La consulta escanearía {scanned / 1024**3:.1f} GiB (límite {config.MAX_BYTES_SCANNED / 1024**3:.0f} GiB).",
                    "refused",
                )
            result = await warehouse.run_query(sql, labels={"origin": "dashboard"})
        except WarehouseError as e:
            raise DashError(str(e), "failed") from e
        meta = [{"label": "Datos escaneados", "value": _bytes_label(result.bytes_processed)}]
        out = {
            "data": result.rows,
            "columns": [c["name"] for c in result.columns],
            "types": {c["name"]: c["type"] for c in result.columns},
            "cut": result.truncated,
            "rows": len(result.rows),
            "meta": meta,
            "ms": int((time.monotonic() - started) * 1000),
            "at": now_iso(),
            "args": args,
        }
        if raw:
            out["answer"] = {
                "schema": {"fields": [{"name": c["name"], "type": c["type"]} for c in result.columns]},
                "rows": [{"f": [{"v": r.get(c["name"])} for c in result.columns]} for r in result.rows],
                "jobComplete": True,
                "totalRows": str(result.total_rows),
                "totalBytesProcessed": str(result.bytes_processed),
            }
        return out

    async def load(
        self, aid: str, ds_id: str, values: dict[str, Any] | None, cache: str = "prefer", raw: bool = False, *, user: str
    ) -> dict[str, Any]:
        """Lo que pide la página de un lector: caché primero y refresco cuando queda viejo."""
        self.require_viewer(aid, user)
        ds = self.store.get(aid, "datasets", ds_id)
        if not ds:
            raise DashError(f"No existe la fuente {ds_id}.", "missing")
        source = ds.get("source") or {}
        if source.get("kind") == "rows":
            raise DashError("Formato viejo (kind: rows): no carga.", "error")
        if source.get("kind") == "file":
            req = ds.get("request")
            if isinstance(req, dict) and sql_guard.params_in_args(req.get("args") or {}):
                raise DashError("Esta consulta cambia con un filtro, así que sus resultados guardados no se pueden mostrar.")
            raw_bytes, name = self.store.get_asset(aid, source.get("url", ""))
            rows, cols = parse_file(raw_bytes, source.get("name") or name, source.get("format"))
            stale_h = ds.get("staleAfterHours")
            updated_at = (ds.get("updated") or {}).get("at")
            stale = False
            if stale_h and updated_at:
                try:
                    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    stale = age.total_seconds() > float(stale_h) * 3600
                except ValueError:
                    pass
            return {"status": "ok", "data": rows, "columns": cols, "cut": False, "rows": len(rows), "meta": [],
                    "at": updated_at, "stale": stale, "kind": "file", "ms": 0}
        merged = self._values(aid, values)
        # Con las credenciales de cada lector la caché es por persona: un resultado no
        # puede llegarle a alguien que no tiene permiso para leer esos datos.
        who = user if self.per_user_cache() else None
        rendered_key = hashlib.sha1(json.dumps([source, merged, who], sort_keys=True, default=str).encode()).hexdigest()[:20]
        key = f"{ds_id}__{'r' if raw else 'p'}{rendered_key}"
        cached = self.store.cache_get(aid, key)
        age = time.time() - cached.get("_ts", 0) if cached else None
        stale = cached is None or age > config.DEFAULT_TTL_SECONDS
        if cached and (cache == "prefer" or (cache == "auto" and not stale)):
            return {**cached, "status": "ok", "stale": stale, "kind": "query"}
        lock = self._locks.setdefault(f"{aid}/{key}", asyncio.Lock())
        async with lock:
            fresh = self.store.cache_get(aid, key)
            if fresh and fresh.get("_ts", 0) > (cached or {}).get("_ts", 0):
                return {**fresh, "status": "ok", "stale": False, "kind": "query"}
            out = await self.run_call(source, merged, raw=raw, user=user)
            if raw:  # con loader, la página arma las filas desde la respuesta
                out.pop("data", None)
            payload = {**out, "_ts": time.time()}
            if _size(payload) > MAX_FILE_BYTES:
                raise DashError("El resultado supera 8 MB: agregá en SQL.", "refused")
            self.store.cache_put(aid, key, payload)
        return {**payload, "status": "ok", "stale": False, "kind": "query"}

    async def run_aux(self, aid: str, ds_id: str, tool: str, args: dict[str, Any], *, user: str) -> Any:
        """call() de un loader: una herramienta auxiliar del mismo conector (por ejemplo,
        buscar el resultado de una consulta que quedó corriendo).

        La herramienta principal no: la página no elige qué SQL corre. Corre con las
        credenciales de quien abre el dashboard, así que una página que pudiera mandar su
        propia consulta leería, con la identidad de cada lector, lo que su autor no puede.
        Lo que consulta un dashboard son sus fuentes, que se ven y se editan en Fuentes.
        """
        self.require_viewer(aid, user)
        ds = self.store.get(aid, "datasets", ds_id) or {}
        reg = CONNECTORS.get((ds.get("source") or {}).get("connector") or "")
        if not reg or tool not in reg["aux"]:
            raise DashError(f"Herramienta no permitida: {tool}. Desde la página solo se usan {', '.join(sorted(reg['aux'])) if reg else 'auxiliares'}.", "refused")
        args = {**(args or {}), "projectId": config.WAREHOUSE_PROJECT}
        try:
            return await self.client_for(user).call_tool(tool, args)
        except WarehouseError as e:
            raise DashError(str(e), "failed") from e

    async def upload_from_request(self, aid: str, user: str, name: str, request: dict[str, Any]) -> dict[str, Any]:
        """Corre una consulta y guarda sus filas como .json (resultados guardados)."""
        self.require_editor(aid, user)
        self._validate_call(request, "request", allow_params=False)
        if sql_guard.params_in_args(request.get("args") or {}):
            raise DashError("Una consulta con filtros no se puede guardar: una sola copia no puede seguir al filtro.")
        out = await self.run_call(request, {}, user=user)
        raw = json.dumps(out["data"], ensure_ascii=False, default=str).encode("utf-8")
        asset = self.store.put_asset(aid, name if name.endswith(".json") else f"{name}.json", raw)
        return {**asset, "rows": out["rows"], "columns": out["columns"], "cut": out["cut"], "at": out["at"]}

    async def run_dataset(self, aid: str, ds_id: str, values: dict[str, Any] | None = None, *, user: str) -> dict[str, Any]:
        """Lo que hace el agente después de escribir: correr la llamada tal como quedó guardada."""
        self.require_viewer(aid, user)
        ds = self.store.get(aid, "datasets", ds_id)
        if not ds:
            raise DashError(f"No existe la fuente {ds_id}.", "missing")
        out = await self.load(aid, ds_id, values, cache="refresh", user=user)
        return out

    # --- editor de Fuentes (solo editores) ---------------------------------------------------
    def _query_source(self, aid: str, ds_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        ds = self.store.get(aid, "datasets", ds_id)
        if not ds:
            raise DashError(f"No existe la fuente {ds_id}.", "missing")
        source = ds.get("source") or {}
        if source.get("kind") != "query":
            raise DashError("Solo se edita el pedido de una consulta en vivo.")
        return ds, source

    async def test_source(self, aid: str, user: str, ds_id: str, args: dict[str, Any], values: dict[str, Any] | None) -> dict[str, Any]:
        self.require_editor(aid, user)
        _, source = self._query_source(aid, ds_id)
        call = {**source, "args": args}
        self._validate_call(call, f"datasets/{ds_id}")
        out = await self.run_call(call, self._values(aid, values), user=user)
        return {k: out[k] for k in ("data", "columns", "rows", "cut", "meta", "ms", "at")}

    def save_source(self, aid: str, user: str, ds_id: str, args: dict[str, Any]) -> dict[str, Any]:
        self.require_editor(aid, user)
        ds, source = self._query_source(aid, ds_id)
        return self.write(aid, user, [{"op": "set", "collection": "datasets", "doc_id": ds_id,
                                       "data": {**ds, "source": {**source, "args": args}}}])

    def source_versions(self, aid: str, ds_id: str, user: str) -> list[dict[str, Any]]:
        self.require_viewer(aid, user)
        return [v for v in self.store.versions(aid, "datasets", ds_id) if isinstance(v["data"], dict)]

    def restore_source(self, aid: str, user: str, ds_id: str, version: str) -> dict[str, Any]:
        self.require_editor(aid, user)
        old = next((v["data"] for v in self.store.versions(aid, "datasets", ds_id) if v["version"] == version), None)
        if old is None:
            raise DashError("No existe esa versión.", "missing")
        return self.write(aid, user, [{"op": "set", "collection": "datasets", "doc_id": ds_id, "data": old}])

    def resume_source(self, aid: str, user: str, ds_id: str) -> dict[str, Any]:
        """Una consulta pausada vuelve a correr en vivo (la página no lo deshace)."""
        self.require_editor(aid, user)
        ds = self.store.get(aid, "datasets", ds_id)
        if not ds or not isinstance(ds.get("request"), dict):
            raise DashError("Esta fuente no es una consulta pausada.")
        req = ds["request"]
        live = {k: v for k, v in ds.items() if k not in ("source", "request", "lastAttempt", "updated", "schedule", "staleAfterHours")}
        live["source"] = {"kind": "query", "connector": req.get("connector"), "tool": req.get("tool"), "args": req.get("args")}
        return self.write(aid, user, [{"op": "set", "collection": "datasets", "doc_id": ds_id, "data": live}])

    # --- corridas --------------------------------------------------------------------------
    def put_runs(self, aid: str, user: str, doc: dict[str, Any]) -> None:
        self.require_viewer(aid, user)
        clean = json.loads(json.dumps(doc, default=str))
        clean["marks"] = (clean.get("marks") or [])[:20]
        clean["errors"] = (clean.get("errors") or [])[:20]
        if _size(clean) > MAX_DOC_BYTES:
            clean.pop("marks", None)
        self.store.put(aid, f"data/users/{user}", "runs", clean)

    # --- para el shell ----------------------------------------------------------------------
    def describe(self, aid: str, user: str) -> dict[str, Any]:
        platform = self.require_viewer(aid, user)
        files = self.store.list_docs(aid, "files")
        datasets = self.store.list_docs(aid, "datasets")
        page_text = "\n".join(f.get("text", "") for f in files.values())
        used = {i for i in datasets if re.search(rf"['\"\s]{re.escape(i)}['\"\s]", page_text)}
        return {
            "contract": CONTRACT,
            "meta": {**(self.store.get(aid, "dash", "meta") or {}), "id": aid, **{k: platform[k] for k in ("owner", "created_at", "updated_at")}},
            "params": self.params_defaults(aid),
            "datasets": datasets,
            "unused": sorted(set(datasets) - used),
            "files": {name: f.get("text", "") for name, f in files.items()},
            "runs": self.store.get(aid, f"data/users/{user}", "runs"),
            "editor": self.can_edit(platform, user),
            "project_id": platform.get("project_id"),
        }


service = DashboardService()
