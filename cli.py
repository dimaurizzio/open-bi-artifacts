"""Comandos de Open BI Artifacts.

    open-bi serve                         levanta la app (http://localhost:8765)
    open-bi demo seed --project P --run   crea el dataset de demo en BigQuery
    open-bi demo knowledge --project P --curator mail
    open-bi backup /ruta/a/backups        backup en caliente de los datos
    open-bi migrate mail                  pasa datos de antes del login a una cuenta

`--env NOMBRE` (en cualquier lugar) carga `.env.NOMBRE` en vez de `.env.local`. También
se puede correr como `python -m agent_dashboards …`.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

# Comandos que delegan en el main() de otro módulo, con sus propios argumentos.
_DELEGADOS = {
    ("demo", "seed"): "agent_dashboards.demo.seed",
    ("demo", "knowledge"): "agent_dashboards.demo.knowledge",
    ("backup",): "agent_dashboards.server.backup",
    ("migrate",): "agent_dashboards.auth.migrate",
}


def _serve(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="open-bi serve", description="Levanta la app.")
    parser.add_argument("--host", default="127.0.0.1", help="Dirección donde escucha (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Puerto (default 8765)")
    parser.add_argument("--reload", action="store_true", help="Reinicia al cambiar el código (desarrollo)")
    args = parser.parse_args(argv)
    import uvicorn

    # Un solo worker: los turnos en curso viven en memoria del proceso (server/runs.py).
    uvicorn.run("agent_dashboards.server.app:app", host=args.host, port=args.port, reload=args.reload, workers=1)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env")
    known, argv = pre.parse_known_args(argv)
    if known.env:
        # Antes de importar nada de la app: config lee APP_ENV al cargarse.
        os.environ["APP_ENV"] = known.env

    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return
    if argv[0] == "serve":
        _serve(argv[1:])
        return
    for prefijo, modulo in _DELEGADOS.items():
        if tuple(argv[: len(prefijo)]) == prefijo:
            sys.argv = ["open-bi " + " ".join(prefijo), *argv[len(prefijo):]]
            importlib.import_module(modulo).main()
            return
    print(f"Comando desconocido: {' '.join(argv)}\n\n{__doc__.strip()}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
