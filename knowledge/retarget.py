"""Mueve el conocimiento de una tabla (y de sus columnas) a otra tabla.

Para corregir entradas guardadas sobre un nombre equivocado, por ejemplo la tabla
de origen en lugar de la habilitada que la lee. El contenido no cambia: solo el
objetivo, y cada entrada queda con un evento `retargeted` en su historia.

    python -m agent_dashboards.knowledge.retarget <tabla_vieja> <tabla_nueva> --actor <mail> [--dry-run]
"""

from __future__ import annotations

import argparse

from .service import service
from .store import clean_ref, now_iso


def plan(old: str, new: str) -> dict:
    old, new = clean_ref(old), clean_ref(new)
    entries = [
        e for e in service.store.entries(target=old)
        if e["target_ref"].lower() == old.lower() or e["target_ref"].lower().startswith(old.lower() + ".")
    ]
    catalog = service.store.catalog(new)
    columns = {c["name"].lower(): c["name"] for c in catalog[0]["columns"]} if len(catalog) == 1 else {}
    moves, missing = [], []
    for e in entries:
        if e["target_type"] == "column":
            col = e["target_ref"].rsplit(".", 1)[1]
            if columns and col.lower() not in columns:
                missing.append(col)
                continue
            moves.append((e, f"{new}.{columns.get(col.lower(), col)}"))
        else:
            moves.append((e, new))
    return {"moves": moves, "missing_columns": sorted(set(missing)), "in_catalog": bool(catalog)}


def apply(old: str, new: str, actor: str) -> dict:
    result = plan(old, new)
    store = service.store
    with store._lock:
        conn = store._conn()
        try:
            with conn:
                for e, target in result["moves"]:
                    conn.execute("UPDATE entries SET target_ref = ?, updated_at = ? WHERE id = ?", (target, now_iso(), e["id"]))
                    conn.execute("UPDATE entries_fts SET target_ref = ? WHERE entry_id = ?", (target, e["id"]))
                    conn.execute(
                        "INSERT INTO events (entry_id, action, actor, at, note) VALUES (?, 'retargeted', ?, ?, ?)",
                        (e["id"], actor, now_iso(), f"{e['target_ref']} → {target}"),
                    )
        finally:
            conn.close()
    service._export_quietly()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Mueve el conocimiento de una tabla a otra.")
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument("--actor", required=True, help="Mail de quien hace el cambio (queda en la historia)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = plan(args.old, args.new) if args.dry_run else apply(args.old, args.new, args.actor)
    verb = "Se moverían" if args.dry_run else "Movidas"
    print(f"{verb}: {len(result['moves'])} entradas")
    if not result["in_catalog"]:
        print(f"Ojo: {args.new} no está en el catálogo.")
    if result["missing_columns"]:
        print("No se mueven (la tabla nueva no tiene esas columnas):", ", ".join(result["missing_columns"]))


if __name__ == "__main__":
    main()
