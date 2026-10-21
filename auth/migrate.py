"""Pasa los datos de un id de usuario a otro (por ejemplo, de "ana" al mail de Google).

Antes del login todo quedaba a nombre de DEFAULT_USER_ID. Esto mueve chats,
dashboards, artifacts viejos y la autoría del conocimiento al mail nuevo. Correrlo
con el servidor apagado:

    open-bi migrate ana@empresa.com            # desde DEFAULT_USER_ID
    open-bi migrate ana@empresa.com --from ana
    open-bi migrate ana@empresa.com --dry-run
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from .. import config
from .store import clean_email


def _sqlite(path: Path, statements: list[tuple[str, str]], old: str, new: str, dry: bool) -> dict[str, int]:
    out: dict[str, int] = {}
    if not path.exists():
        return out
    conn = sqlite3.connect(path)
    try:
        with conn:
            for label, sql in statements:
                try:
                    count = conn.execute(sql.replace("UPDATE", "SELECT count(*) FROM", 1).split(" SET ")[0]
                                         + " WHERE " + sql.split(" WHERE ", 1)[1], (old,)).fetchone()[0]
                except sqlite3.OperationalError:
                    continue  # la tabla no existe en esta base
                out[label] = count
                if count and not dry:
                    conn.execute(sql, (new, old))
    finally:
        conn.close()
    return out


def migrate(old: str, new: str, dry: bool = False) -> dict[str, int]:
    new = clean_email(new)
    result: dict[str, int] = {}
    result |= _sqlite(config.SESSIONS_DB, [
        ("chats", "UPDATE sessions SET user_id = ? WHERE user_id = ?"),
        ("eventos de chat", "UPDATE events SET user_id = ? WHERE user_id = ?"),
        ("estado de usuario", "UPDATE user_states SET user_id = ? WHERE user_id = ?"),
    ], old, new, dry)
    result |= _sqlite(config.KNOWLEDGE_DB, [
        ("conocimiento (autor)", "UPDATE entries SET author_id = ? WHERE author_id = ?"),
        ("conocimiento (revisor)", "UPDATE entries SET reviewed_by = ? WHERE reviewed_by = ?"),
        ("conocimiento (eventos)", "UPDATE events SET actor = ? WHERE actor = ?"),
    ], old, new, dry)

    dashboards = runs = legacy = 0
    root = Path(config.ARTIFACTS_DIR)
    for d in root.iterdir() if root.exists() else []:
        platform = d / "_platform.json"
        if platform.exists():
            p = json.loads(platform.read_text(encoding="utf-8"))
            if p.get("owner") == old:
                dashboards += 1
                if not dry:
                    p["owner"] = new
                    platform.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
            user_dir = d / "db" / "data" / "users" / old
            if user_dir.exists():
                runs += 1
                target = user_dir.with_name(new)
                if not dry and not target.exists():
                    user_dir.rename(target)
        meta = d / "dash" / "meta.json"
        if meta.exists():
            m = json.loads(meta.read_text(encoding="utf-8"))
            if m.get("owner") == old:
                legacy += 1
                if not dry:
                    m["owner"] = new
                    meta.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    result |= {"dashboards": dashboards, "corridas de lector": runs, "artifacts viejos": legacy}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Pasa los datos de un usuario a otro id (el mail de Google).")
    parser.add_argument("to", help="Mail de Google que va a ser el dueño")
    parser.add_argument("--from", dest="old", default=config.DEFAULT_USER_ID, help=f"Id actual (default {config.DEFAULT_USER_ID})")
    parser.add_argument("--dry-run", action="store_true", help="Solo cuenta, no cambia nada")
    args = parser.parse_args()
    result = migrate(args.old, args.to, dry=args.dry_run)
    verb = "Se moverían" if args.dry_run else "Movido"
    for k, v in result.items():
        print(f"{verb}: {v} {k}")


if __name__ == "__main__":
    main()
