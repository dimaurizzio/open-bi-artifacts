"""Exportación del conocimiento compartido y aprobado a un bundle OKF v0.2.

Exportación de una sola vía: la base es la fuente de verdad y el bundle se
regenera entero (no se edita a mano ni se reimporta). Las notas privadas, las
propuestas sin aprobar y lo deprecado no se exportan.

    <dir>/
    ├── index.md     ← okf_version y una línea por concepto
    ├── log.md       ← altas y bajas por fecha
    ├── tables/      ← una por tabla: esquema del catálogo + conocimiento de la tabla y sus columnas
    ├── metrics/
    ├── terms/
    └── joins/

Un archivo junta varias entradas (de distintos autores), así que el frontmatter
lleva `generated` = este exportador, `verified` = quienes aprobaron, y el detalle
por entrada (autor, aprobación, vencimiento) en la clave propia `entries`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .store import KnowledgeStore, now_iso

PRODUCER = "agent-dashboards/0.1"
OKF_VERSION = "0.2"
MARKER = ".okf-export.json"  # marca que la carpeta la genera este exportador (se puede reemplazar entera)

FOLDERS = {"table": "tables", "metric": "metrics", "term": "terms", "join": "joins"}
TYPE_NAMES = {"table": "BigQuery Table", "metric": "Metric", "term": "Term", "join": "Join"}
SECTION_TITLES = {"tables": "Tablas", "metrics": "Métricas", "terms": "Términos", "joins": "Joins"}
KIND_LABELS = {
    "descripcion": "Descripción",
    "definicion_metrica": "Definición de métrica",
    "regla_filtro": "Regla de filtro",
    "advertencia": "Advertencia",
    "join": "Join",
    "sinonimo": "Sinónimo",
    "consulta_ejemplo": "Consulta de ejemplo",
}


class ExportError(RuntimeError):
    pass


# --- YAML mínimo (los escalares van como JSON, que también es YAML válido) -------------


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(str(v), ensure_ascii=False)


def _yaml(value: Any, indent: int = 0) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines += _yaml(v, indent + 2)
            else:
                lines.append(f"{pad}{k}: {_scalar(v) if not isinstance(v, (dict, list)) else ('{}' if isinstance(v, dict) else '[]')}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                sub = _yaml(item, indent + 2)
                lines.append(f"{pad}- {sub[0].lstrip()}")
                lines += sub[1:]
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    return lines


def frontmatter(data: dict[str, Any]) -> str:
    return "---\n" + "\n".join(_yaml({k: v for k, v in data.items() if v not in (None, "", [], {})})) + "\n---\n"


# --- helpers -----------------------------------------------------------------------------


def slug(ref: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", ref.lower()).strip("-.") or "concepto"


def _ts(date_or_iso: str | None) -> str | None:
    """Fecha u hora ISO → datetime ISO con UTC explícito (lo que pide OKF v0.2)."""
    if not date_or_iso:
        return None
    text = str(date_or_iso)
    return f"{text[:10]}T00:00:00Z" if len(text) == 10 else text


def _first_line(text: str, limit: int = 200) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _human(user: str | None) -> str | None:
    return f"human:{user}" if user else None


def _md_cell(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


# --- armado de los documentos ------------------------------------------------------------


def _documents(store: KnowledgeStore) -> dict[tuple[str, str], dict[str, Any]]:
    docs: dict[tuple[str, str], dict[str, Any]] = {}
    for t in store.catalog():
        docs[("tables", t["ref"].lower())] = {"folder": "tables", "target_type": "table", "ref": t["ref"], "catalog": t, "entries": []}
    for e in store.entries(statuses=("approved",), visibility="shared"):
        if e["target_type"] == "column":
            ref, ttype = e["target_ref"].rsplit(".", 1)[0], "table"
        else:
            ref, ttype = e["target_ref"], e["target_type"]
        folder = FOLDERS[ttype]
        doc = docs.setdefault(
            (folder, ref.lower()), {"folder": folder, "target_type": ttype, "ref": ref, "catalog": None, "entries": []}
        )
        doc["entries"].append(e)
    for doc in docs.values():
        doc["entries"].sort(key=lambda e: (e["target_type"] != "table", e["target_ref"].lower(), e["created_at"]))
        doc["name"] = doc["catalog"]["name"] if doc["catalog"] else doc["ref"].rsplit(".", 1)[-1] if doc["target_type"] == "table" else doc["ref"]
        doc["path"] = f"{doc['folder']}/{slug(doc['ref'])}.md"
    return docs


def _description(doc: dict[str, Any]) -> str:
    t = doc["catalog"]
    if t and t.get("description"):
        return _first_line(t["description"])
    for e in doc["entries"]:
        if e["kind"] in ("descripcion", "definicion_metrica") and e["target_type"] != "column":
            return _first_line(e["content"])
    if doc["entries"]:
        return _first_line(doc["entries"][0]["content"])
    if t:
        return f"Tabla {t['name']} del dataset {t['dataset']}."
    return ""


def _related(doc: dict[str, Any], docs: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    text = " ".join(f"{e['content']} {e.get('sql') or ''}" for e in doc["entries"]).lower()
    if not text.strip():
        return []
    out = []
    for other in docs.values():
        if other is doc:
            continue
        needles = {other["ref"].lower()}
        if len(other["name"]) >= 4:
            needles.add(other["name"].lower())
        if any(re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])", text) for n in needles):
            out.append(other)
    return sorted(out, key=lambda d: d["path"])


def _render(doc: dict[str, Any], docs: dict[tuple[str, str], dict[str, Any]]) -> str:
    t = doc["catalog"]
    entries = doc["entries"]
    sources: list[dict[str, Any]] = []
    source_ids: dict[str, str] = {}
    for e in entries:
        for s in e.get("sources") or []:
            key = s.get("resource") or s.get("title") or ""
            if key and key not in source_ids:
                source_ids[key] = f"s{len(source_ids) + 1}"
                src = {"id": source_ids[key], "resource": s.get("resource") or s.get("title") or ""}
                if s.get("title"):
                    src["title"] = s["title"]
                sources.append(src)

    changes = [e["reviewed_at"] for e in entries if e.get("reviewed_at")] + ([t["synced_at"]] if t else [])
    verified, seen = [], set()
    for e in entries:
        if e.get("reviewed_by") and e["reviewed_by"] not in seen:
            seen.add(e["reviewed_by"])
            verified.append({"by": _human(e["reviewed_by"]), "at": e["reviewed_at"]})
    stale = sorted(e["stale_after"] for e in entries if e.get("stale_after"))
    tags = sorted({doc["folder"][:-1]} | ({t["dataset"]} if t else set()) | {e["kind"] for e in entries})
    resource = f"bigquery://{doc['ref']}" if doc["target_type"] == "table" else f"kb://{doc['target_type']}/{doc['ref']}"

    fm = {
        "type": TYPE_NAMES[doc["target_type"]],
        "title": doc["name"],
        "description": _description(doc),
        "resource": resource,
        "tags": tags,
        "generated": {"by": PRODUCER, "at": max(changes) if changes else now_iso()},
        "verified": verified,
        "status": "stable",
        "sources": sources,
        "stale_after": _ts(stale[0]) if stale else None,
        "entries": [
            {
                "id": e["id"],
                "kind": e["kind"],
                "target": e["target_ref"],
                "generated": {"by": _human(e["author_id"]), "at": e["created_at"]},
                "verified": {"by": _human(e["reviewed_by"]), "at": e["reviewed_at"]},
                "proposed_via": e["proposed_via"],
                **({"stale_after": _ts(e["stale_after"])} if e.get("stale_after") else {}),
            }
            for e in entries
        ],
    }
    body: list[str] = []
    if t:
        body += ["# Esquema", ""]
        if t.get("description"):
            body += [t["description"].strip(), ""]
        facts = [f"Tipo: {t.get('table_type') or 'TABLE'}"]
        if t.get("num_rows") is not None:
            facts.append(f"Filas: {t['num_rows']:,}".replace(",", "."))
        if t.get("partition_field") or t.get("partition_type"):
            facts.append(f"Partición: `{t.get('partition_field') or '_PARTITIONTIME'}` ({t.get('partition_type') or 'DAY'})")
        if t.get("clustering"):
            facts.append("Clustering: " + ", ".join(f"`{c}`" for c in t["clustering"]))
        if t.get("last_modified"):
            facts.append(f"Actualizada: {t['last_modified'][:10]}")
        body += ["* " + f for f in facts] + [""]
        if t.get("columns"):
            body += ["| Columna | Tipo | Descripción |", "| --- | --- | --- |"]
            body += [f"| `{c['name']}` | {c.get('type', '')} | {_md_cell(c.get('description'))} |" for c in t["columns"]]
            body.append("")
    if entries:
        body += ["# Conocimiento", ""]
        for e in entries:
            title = KIND_LABELS.get(e["kind"], e["kind"])
            if e["target_type"] == "column":
                title += f" · columna `{e['target_ref'].rsplit('.', 1)[1]}`"
            body += [f"## {title}", "", e["content"].strip(), ""]
            if e.get("sql"):
                body += ["```sql", e["sql"].strip(), "```", ""]
            meta = [f"autor {_human(e['author_id'])}", f"aprobado por {_human(e['reviewed_by'])} el {(e['reviewed_at'] or '')[:10]}"]
            if e.get("stale_after"):
                meta.append(f"vence el {e['stale_after']}")
            meta.append(f"id `{e['id']}`")
            refs = "".join(
                f"[^{source_ids[k]}]" for s in e.get("sources") or [] if (k := s.get("resource") or s.get("title") or "") in source_ids
            )
            body += [f"_{' · '.join(meta)}_{' ' + refs if refs else ''}", ""]
    related = _related(doc, docs)
    if related:
        body += ["# Related", ""]
        body += [f"* [{r['name']}](../{r['path']})" for r in related] + [""]
    if sources:
        body += ["# Citations", ""]
        for s in sources:
            label = s.get("title") or s["resource"]
            link = f"[{label}]({s['resource']})" if re.match(r"^[a-z][a-z0-9+.-]*://", s["resource"]) else label
            body.append(f"[^{s['id']}]: {link}")
        body.append("")
    return frontmatter(fm) + "\n" + "\n".join(body).rstrip() + "\n"


def _index(docs: list[dict[str, Any]], root: bool, folder: str | None = None) -> str:
    lines: list[str] = []
    for f in ("tables", "metrics", "terms", "joins"):
        group = [d for d in docs if d["folder"] == f]
        if not group or (folder and f != folder):
            continue
        lines += [f"# {SECTION_TITLES[f]}", ""]
        for d in sorted(group, key=lambda d: d["name"].lower()):
            link = d["path"] if root else d["path"].split("/", 1)[1]
            desc = _description(d)
            lines.append(f"* [{d['name']}]({link})" + (f" - {desc}" if desc else ""))
        lines.append("")
    if not lines:
        lines = ["# Conocimiento", "", "Todavía no hay conceptos exportados.", ""]
    head = frontmatter({"okf_version": OKF_VERSION}) + "\n" if root else ""
    return head + "\n".join(lines).rstrip() + "\n"


def _log(store: KnowledgeStore, docs: dict[tuple[str, str], dict[str, Any]]) -> str:
    shared = {e["id"]: e for e in store.entries(visibility="shared")}
    paths = {}
    for d in docs.values():
        for e in d["entries"]:
            paths[e["id"]] = d
    by_day: dict[str, list[str]] = {}
    for ev in reversed(store.events(list(shared))):
        e = shared[ev["entry_id"]]
        action = ev["action"]
        if action == "approved":
            word = "Alta"
        elif action == "superseded":
            word = "Reemplazo"
        elif action == "deprecated" and e.get("reviewed_at"):
            word = "Baja"
        else:
            continue
        target = e["target_ref"]
        doc = paths.get(e["id"]) or docs.get((FOLDERS["table" if e["target_type"] == "column" else e["target_type"]],
                                              (target.rsplit(".", 1)[0] if e["target_type"] == "column" else target).lower()))
        where = f"[{doc['name']}]({doc['path']})" if doc else f"`{target}`"
        kind = KIND_LABELS.get(e["kind"], e["kind"]).lower()
        if word == "Alta":
            text = f"{kind} sobre {where} (autor {e['author_id']}, aprobó {ev['actor']})."
        elif word == "Reemplazo":
            text = f"{kind} sobre {where} reemplazada por `{ev.get('note') or ''}` ({ev['actor']})."
        else:
            text = f"{kind} sobre {where} deprecada por {ev['actor']}."
        by_day.setdefault(ev["at"][:10], []).append(f"* **{word}**: {text[0].upper()}{text[1:]}")
    lines = ["# Registro de cambios", ""]
    for day in sorted(by_day, reverse=True):
        lines += [f"## {day}", ""] + by_day[day] + [""]
    if len(lines) == 2:
        lines += ["Sin cambios todavía.", ""]
    return "\n".join(lines).rstrip() + "\n"


# --- escritura ------------------------------------------------------------------------


def _check_target(out: Path) -> None:
    """Solo se reemplaza una carpeta vacía o una que generó este exportador."""
    if out.exists() and any(out.iterdir()) and not (out / MARKER).exists():
        raise ExportError(f"{out} tiene archivos que no generó el exportador: no la reemplazo.")


def export_bundle(store: KnowledgeStore, out_dir: Path | str) -> dict[str, Any]:
    out = Path(out_dir)
    _check_target(out)
    docs = _documents(store)
    files: dict[str, str] = {d["path"]: _render(d, docs) for d in docs.values()}
    doc_list = list(docs.values())
    files["index.md"] = _index(doc_list, root=True)
    for folder in sorted({d["folder"] for d in doc_list}):
        files[f"{folder}/index.md"] = _index(doc_list, root=False, folder=folder)
    files["log.md"] = _log(store, docs)
    result = {
        "at": now_iso(),
        "dir": str(out),
        "files": len(files),
        "concepts": len(doc_list),
        "entries": sum(len(d["entries"]) for d in doc_list),
    }
    tmp = out.with_name(out.name + ".tmp-" + dt.datetime.now().strftime("%H%M%S%f"))
    tmp.mkdir(parents=True)
    try:
        for path, text in files.items():
            target = tmp / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        (tmp / MARKER).write_text(json.dumps({"producer": PRODUCER, **result}, ensure_ascii=False, indent=1), encoding="utf-8")
        if out.exists():
            shutil.rmtree(out)
        tmp.rename(out)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return result


def check_conformance(out_dir: Path | str) -> list[str]:
    """Reglas de conformidad de OKF (§9): frontmatter parseable con `type` en cada
    concepto, e index.md / log.md con su estructura. Devuelve los problemas."""
    problems: list[str] = []
    base = Path(out_dir)
    for f in sorted(base.rglob("*.md")):
        rel = f.relative_to(base).as_posix()
        text = f.read_text(encoding="utf-8")
        if f.name == "index.md":
            if text.startswith("---") and rel != "index.md":
                problems.append(f"{rel}: solo el index.md raíz lleva frontmatter")
            body = text.split("---\n", 2)[-1] if text.startswith("---") else text
            for line in body.splitlines():
                if line.startswith("* ") and not re.match(r"^\* \[[^\]]+\]\([^)]+\)( - .+)?$", line):
                    problems.append(f"{rel}: entrada mal formada: {line}")
        elif f.name == "log.md":
            for line in text.splitlines():
                if line.startswith("## ") and not re.match(r"^## \d{4}-\d{2}-\d{2}", line):
                    problems.append(f"{rel}: encabezado sin fecha ISO: {line}")
        else:
            m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
            if not m:
                problems.append(f"{rel}: sin frontmatter")
                continue
            type_line = re.search(r'^type: "(.+)"$', m.group(1), re.M)
            if not type_line or not type_line.group(1).strip():
                problems.append(f"{rel}: sin type")
    return problems
