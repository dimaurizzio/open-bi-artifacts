"""Almacén de documentos de cada artifact.

El agente no publica un HTML suelto: escribe documentos con nombre y el visor los
interpreta.

    dash/meta        título, descripción, tipo (dashboard | analysis)
    dash/params      filtros del visor
    datasets/<id>    una fuente: conector, herramienta, SQL, modo
    files/index.html la página (HTML + JS con d3 v7)

Además la plataforma guarda (el agente no los escribe): resultados guardados,
caché de consultas en vivo, el documento de corridas e historial de versiones.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any

from .. import config


class StoreError(ValueError):
    """Violación de una regla forzada del almacén."""


DOC_PATTERNS = {
    "dash/meta": re.compile(r"^dash/meta$"),
    "dash/params": re.compile(r"^dash/params$"),
    "datasets": re.compile(r"^datasets/([a-z][a-z0-9_]{0,63})$"),
    "page": re.compile(r"^files/index\.html$"),
}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{2,80}$")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def doc_kind(path: str) -> str:
    for kind, pattern in DOC_PATTERNS.items():
        if pattern.match(path):
            return kind
    raise StoreError(
        f"Ruta inválida '{path}'. Válidas: dash/meta, dash/params, datasets/<id> (minúsculas,"
        " dígitos y _), files/index.html."
    )


def _slug(text: str) -> str:
    text = text.lower()
    for a, b in zip("áéíóúüñ", "aeiouun"):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or "artifact"


class ArtifactStore:
    def __init__(self, root: Path = config.ARTIFACTS_DIR):
        self.root = Path(root)
        self._lock = threading.RLock()

    # --- rutas -----------------------------------------------------------------

    def _dir(self, artifact_id: str) -> Path:
        if not _ID_RE.match(artifact_id or ""):
            raise StoreError(f"Id de artifact inválido: {artifact_id!r}")
        return self.root / artifact_id

    def _doc_file(self, artifact_id: str, path: str) -> Path:
        kind = doc_kind(path)
        base = self._dir(artifact_id)
        return base / ("files/index.html" if kind == "page" else f"{path}.json")

    def _write_atomic(self, file: Path, data: str) -> None:
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(file.suffix + f".{secrets.token_hex(4)}.tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, file)

    # --- artifacts ------------------------------------------------------------

    def exists(self, artifact_id: str) -> bool:
        return (self._dir(artifact_id) / "dash" / "meta.json").exists()

    def create(self, *, kind: str, title: str, description: str, owner: str) -> dict[str, Any]:
        artifact_id = f"{_slug(title)}-{secrets.token_hex(3)}"
        meta = {
            "id": artifact_id,
            "kind": kind,
            "title": title,
            "description": description,
            "owner": owner,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        with self._lock:
            self._write_atomic(self._dir(artifact_id) / "dash" / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
        return meta

    def list(self, owner: str | None = None) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        metas = []
        for d in self.root.iterdir():
            meta_file = d / "dash" / "meta.json"
            if meta_file.exists():
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if owner is None or meta.get("owner") == owner:
                    metas.append(meta)
        return sorted(metas, key=lambda m: m.get("updated_at", ""), reverse=True)

    def touch(self, artifact_id: str) -> None:
        meta = self.read_json(artifact_id, "dash/meta") or {}
        meta["updated_at"] = now_iso()
        self._write_atomic(self._dir(artifact_id) / "dash" / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))

    # --- documentos -----------------------------------------------------------

    def read_doc(self, artifact_id: str, path: str) -> str | None:
        file = self._doc_file(artifact_id, path)
        return file.read_text(encoding="utf-8") if file.exists() else None

    def read_json(self, artifact_id: str, path: str) -> dict[str, Any] | None:
        text = self.read_doc(artifact_id, path)
        return json.loads(text) if text else None

    def write_doc(self, artifact_id: str, path: str, content: str) -> None:
        size = len(content.encode("utf-8"))
        if size > config.MAX_DOC_BYTES:
            raise StoreError(
                f"El documento {path} pesa {size / 1024:.0f} KB y el máximo es"
                f" {config.MAX_DOC_BYTES // 1024} KB."
            )
        if not self.exists(artifact_id):
            raise StoreError(f"No existe el artifact {artifact_id}.")
        file = self._doc_file(artifact_id, path)
        with self._lock:
            if file.exists() and path != "dash/meta":
                stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S%f")
                history = self._dir(artifact_id) / "history" / f"{stamp}__{path.replace('/', '__')}"
                history.parent.mkdir(parents=True, exist_ok=True)
                history.write_text(file.read_text(encoding="utf-8"), encoding="utf-8")
            self._write_atomic(file, content)
            self.touch(artifact_id)

    def delete_doc(self, artifact_id: str, path: str) -> bool:
        file = self._doc_file(artifact_id, path)
        if file.exists():
            file.unlink()
            return True
        return False

    def datasets(self, artifact_id: str) -> dict[str, dict[str, Any]]:
        folder = self._dir(artifact_id) / "datasets"
        if not folder.exists():
            return {}
        return {
            f.stem: json.loads(f.read_text(encoding="utf-8"))
            for f in sorted(folder.glob("*.json"))
        }

    def list_docs(self, artifact_id: str) -> list[str]:
        docs = []
        base = self._dir(artifact_id)
        for path in ("dash/meta", "dash/params"):
            if self._doc_file(artifact_id, path).exists():
                docs.append(path)
        docs += [f"datasets/{ds}" for ds in self.datasets(artifact_id)]
        if (base / "files" / "index.html").exists():
            docs.append("files/index.html")
        return docs

    # --- datos que escribe la plataforma --------------------------------------

    def _data_file(self, artifact_id: str, folder: str, name: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
        return self._dir(artifact_id) / folder / f"{safe}.json"

    def write_data(self, artifact_id: str, folder: str, name: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str)
        if len(data.encode("utf-8")) > config.MAX_RESULT_BYTES:
            raise StoreError(f"El resultado supera {config.MAX_RESULT_BYTES // 1024**2} MB.")
        self._write_atomic(self._data_file(artifact_id, folder, name), data)

    def read_data(self, artifact_id: str, folder: str, name: str) -> dict[str, Any] | None:
        file = self._data_file(artifact_id, folder, name)
        return json.loads(file.read_text(encoding="utf-8")) if file.exists() else None

    def append_run(self, artifact_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            runs = self.read_runs(artifact_id)
            runs.append(record)
            runs = runs[-config.MAX_RUN_RECORDS :]
            self._write_atomic(self._dir(artifact_id) / "runs.json", json.dumps(runs, ensure_ascii=False, default=str))

    def read_runs(self, artifact_id: str) -> list[dict[str, Any]]:
        file = self._dir(artifact_id) / "runs.json"
        return json.loads(file.read_text(encoding="utf-8")) if file.exists() else []


store = ArtifactStore()
