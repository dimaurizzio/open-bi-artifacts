"""Backup en caliente de los datos de la app en un .tar.gz.

Las bases SQLite se copian con la API de backup de SQLite (consistente aunque la
app esté escribiendo); los artifacts y el bundle OKF se copian tal cual.

    open-bi backup /data/backups [--keep 14]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from .. import config

DATABASES = ("sessions.db", "knowledge.db")
FOLDERS = ("artifacts", "knowledge-okf")
FILES = ("feedback.jsonl",)


def _copy_sqlite(src: Path, dst: Path) -> None:
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def backup(out_dir: Path, data_dir: Path = config.DATA_DIR, keep: int = 14) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = out_dir / f"agent-dashboards-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(archive, "w:gz") as tar:
        for name in DATABASES:
            src = data_dir / name
            if src.exists():
                copy = Path(tmp) / name
                _copy_sqlite(src, copy)
                tar.add(copy, arcname=name)
        for name in FOLDERS + FILES:
            src = data_dir / name
            if src.exists():
                tar.add(src, arcname=name)
    # Rotación: se quedan los `keep` más nuevos.
    old = sorted(out_dir.glob("agent-dashboards-*.tar.gz"), reverse=True)[keep:]
    for f in old:
        f.unlink()
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path, help="Carpeta donde dejar el backup")
    parser.add_argument("--keep", type=int, default=14, help="Cuántos backups conservar (default 14)")
    args = parser.parse_args()
    print(backup(args.out_dir, keep=args.keep))


if __name__ == "__main__":
    main()
