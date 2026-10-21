"""Conocimiento de demo para la tienda Andes Outdoor: lo que un equipo ya sabe de sus datos.

Carga entradas compartidas y aprobadas (definiciones, reglas de filtro, advertencias,
joins) para que la demo muestre cómo el agente las usa. Va con el dataset que crea
`demo.seed`, y ese dataset tiene que estar en WAREHOUSE_DATASETS.

    open-bi demo knowledge --project mi-proyecto --curator vos@empresa.com
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from typing import Any

from . import seed


def entries(project: str, dataset: str, today: dt.date | None = None) -> list[dict[str, Any]]:
    today = today or dt.date.today()
    t = f"{project}.{dataset}"
    duplicated = today - dt.timedelta(days=seed.DUPLICATED_DAYS_AGO)
    cyber = today - dt.timedelta(days=seed.SPIKE_DAYS_AGO)
    until = (today + dt.timedelta(days=seed.DAYS)).isoformat()
    src = ["Guía de datos de Andes Outdoor (demo)"]
    return [
        {"target_type": "table", "target_ref": f"{t}.pedidos", "kind": "descripcion",
         "content": "Una fila por pedido de la tienda online (web, app y marketplace). Es la fuente de pedidos, clientes y ticket promedio.",
         "sources": src},
        {"target_type": "table", "target_ref": f"{t}.pedidos", "kind": "regla_filtro",
         "content": "Para medir ventas, excluir siempre los pedidos cancelados: estado <> 'Cancelado'. Son cerca del 6%.",
         "sources": src},
        {"target_type": "table", "target_ref": f"{t}.pedidos", "kind": "advertencia",
         "content": f"El {duplicated:%d/%m/%Y} los pedidos se cargaron dos veces por un error del proceso de carga: ese día cuenta el doble. "
                    "Contar con COUNT(DISTINCT pedido_id), o tomar los montos de items_pedido, que está bien.",
         "sources": src, "stale_after": until},
        {"target_type": "column", "target_ref": f"{t}.pedidos.cupon", "kind": "advertencia",
         "content": f"El {cyber:%d/%m/%Y} fue el Cyber Day: cupón CYBER30 (30% de descuento) y casi el triple de pedidos. "
                    "No es una tendencia: para comparar semanas, sacar ese día o mirarlo aparte.",
         "sources": src, "stale_after": until},
        {"target_type": "table", "target_ref": f"{t}.items_pedido", "kind": "descripcion",
         "content": "Una fila por línea de pedido. Tiene el descuento y si la línea se devolvió: de acá salen los ingresos netos.",
         "sources": src},
        {"target_type": "column", "target_ref": f"{t}.items_pedido.devuelto", "kind": "advertencia",
         "content": "Calzado tiene cerca del 17% de líneas devueltas (por talle); el resto, alrededor del 4%. "
                    "Comparar categorías por ingresos netos, no brutos.",
         "sources": src},
        {"target_type": "table", "target_ref": f"{t}.eventos_web", "kind": "regla_filtro",
         "content": "Para medir el embudo, excluir los bots: es_bot = FALSE. Los bots solo ven productos y son cerca de un cuarto de los eventos.",
         "sources": src},
        {"target_type": "table", "target_ref": f"{t}.ventas_diarias", "kind": "descripcion",
         "content": "Agregado diario sin cancelados, por canal y categoría. Para tendencias de ventas, preferirla a pedidos: es mucho más barata.",
         "sources": src},
        {"target_type": "metric", "target_ref": "ingresos netos", "kind": "definicion_metrica",
         "content": "Ingresos netos: ventas con descuentos, sin pedidos cancelados y sin líneas devueltas.",
         "sql": f"SELECT SUM(i.precio_unitario_usd * i.cantidad - i.descuento_usd) AS ingresos_netos_usd "
                f"FROM `{t}.items_pedido` AS i JOIN (SELECT DISTINCT pedido_id, estado FROM `{t}.pedidos`) AS p USING (pedido_id) "
                "WHERE p.estado <> 'Cancelado' AND NOT i.devuelto AND i.fecha >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)",
         "sources": src},
        {"target_type": "metric", "target_ref": "tasa de conversión", "kind": "definicion_metrica",
         "content": "Tasa de conversión: sesiones con una compra sobre el total de sesiones, sin bots.",
         "sql": f"SELECT fecha, COUNT(DISTINCT IF(evento = 'compra', sesion_id, NULL)) / COUNT(DISTINCT sesion_id) AS conversion "
                f"FROM `{t}.eventos_web` WHERE NOT es_bot AND fecha >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) GROUP BY fecha",
         "sources": src},
        {"target_type": "term", "target_ref": "ticket promedio", "kind": "descripcion",
         "content": "Ticket promedio: total_usd promedio de los pedidos no cancelados, contando cada pedido una sola vez.",
         "sources": src},
        {"target_type": "join", "target_ref": "pedidos, ítems y productos", "kind": "join",
         "content": "pedidos.pedido_id = items_pedido.pedido_id, e items_pedido.producto_id = productos.producto_id da la categoría y el costo. "
                    "pedidos.cliente_id = clientes.cliente_id da el segmento y el canal de adquisición.",
         "sources": src},
    ]


async def load(service, project: str, dataset: str, curator: str) -> list[str]:
    """Propone y aprueba cada entrada. Las que ya existen se saltean (se puede correr dos veces)."""
    from ..knowledge.service import KnowledgeError

    done = []
    for raw in entries(project, dataset):
        try:
            out = await service.propose({**raw, "visibility": "shared"}, curator)
        except KnowledgeError as e:
            if "Ya existe" in str(e):
                continue
            raise
        await service.review(out["entry"]["id"], curator, True)
        done.append(out["entry"]["id"])
    return done


def main() -> None:
    from .. import config
    from ..knowledge.service import service

    parser = argparse.ArgumentParser(description="Carga el conocimiento de demo de Andes Outdoor.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", default=seed.DEFAULT_DATASET)
    parser.add_argument("--curator", required=True, help="Mail del curador que figura como autor y aprobador")
    args = parser.parse_args()
    if f"{args.project}.{args.dataset}".lower() not in {d.lower() for d in config.WAREHOUSE_DATASETS}:
        raise SystemExit(f"Sumá {args.project}.{args.dataset} a WAREHOUSE_DATASETS antes de cargar el conocimiento.")
    if args.curator.lower() not in config.KNOWLEDGE_CURATORS | config.ADMIN_EMAILS:
        raise SystemExit("El curador tiene que estar en KNOWLEDGE_CURATORS o ADMIN_EMAILS.")
    service.extra_curator = lambda user: user.lower() in config.ADMIN_EMAILS
    ids = asyncio.run(load(service, args.project, args.dataset, args.curator.lower()))
    print(f"Cargadas y aprobadas: {len(ids)} entradas.")


if __name__ == "__main__":
    main()
