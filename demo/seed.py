"""Dataset de demo en BigQuery: "Andes Outdoor", una tienda online ficticia de equipo outdoor.

Genera datos sintéticos y deterministas (siempre los mismos para la misma fecha)
para mostrar la plataforma sin datos reales:

| Tabla | Qué es |
| --- | --- |
| `productos` | Catálogo: categoría, marca, precio y costo |
| `clientes` | Clientes: alta, país, canal de adquisición, segmento |
| `pedidos` | Una fila por pedido, particionada por `fecha` |
| `items_pedido` | Una fila por línea de pedido (producto, cantidad, descuento, devolución) |
| `eventos_web` | Embudo del sitio y la app: vista de producto, carrito, checkout, compra |
| `ventas_diarias` | Agregado por día, canal y categoría, sin cancelados |

Trae a propósito cosas para descubrir en una demo: estacionalidad semanal, un Cyber
Day con el cupón CYBER30, bots en los eventos web, una categoría con muchas
devoluciones (Calzado) y un día en que los pedidos se cargaron dos veces.

Funciona en el sandbox gratuito de BigQuery (sin facturación): solo usa
`CREATE TABLE … AS SELECT` (el sandbox no admite DML) y cubre los últimos 56
días, porque el sandbox borra las particiones y tablas de más de 60 días. Para
refrescar la demo, se vuelve a correr.

    python -m agent_dashboards.demo.seed --project mi-proyecto            # imprime el SQL
    python -m agent_dashboards.demo.seed --project mi-proyecto --run      # lo corre con `bq`
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

DEFAULT_DATASET = "demo_andes_outdoor"
DAYS = 56  # el sandbox borra lo de más de 60 días
SPIKE_DAYS_AGO = 9  # el Cyber Day
DUPLICATED_DAYS_AGO = 23  # el día en que los pedidos se cargaron dos veces
PRODUCTS = 300
CUSTOMERS = 25_000
BASE_ORDERS = 900  # pedidos por día hábil al principio del período
BASE_SESSIONS = 6_000  # sesiones web por día hábil al principio del período

CATEGORIES = ["Camperas", "Calzado", "Mochilas", "Carpas", "Ropa térmica", "Accesorios"]
NOUNS = {
    "Camperas": ["Campera", "Rompevientos", "Chaleco"],
    "Calzado": ["Zapatilla trekking", "Bota", "Sandalia"],
    "Mochilas": ["Mochila", "Bolso", "Riñonera"],
    "Carpas": ["Carpa", "Bolsa de dormir", "Aislante"],
    "Ropa térmica": ["Primera capa", "Polar", "Calza térmica"],
    "Accesorios": ["Linterna frontal", "Botella", "Gorro", "Guantes", "Bastones"],
}
# Precio base y rango (USD) por categoría.
PRICES = {"Camperas": (90, 160), "Calzado": (70, 110), "Mochilas": (35, 90), "Carpas": (60, 260), "Ropa térmica": (25, 55), "Accesorios": (8, 40)}
MODELS = ["Cumbre", "Glaciar", "Viento Sur", "Aconcagua", "Lago Azul", "Bosque", "Nevado", "Cóndor", "Puna", "Huemul"]
BRANDS = ["Andes Outdoor", "Kaiken", "Huella", "Cumbre Pro"]
COUNTRIES = ["Argentina"] * 12 + ["Chile", "Chile", "Uruguay", "Perú", "Colombia", "México"]


def rnd(key: str, salt: str) -> str:
    """Número pseudoaleatorio en [0, 1) a partir de una clave: mismo dato en cada corrida."""
    return f"(MOD(ABS(FARM_FINGERPRINT(CONCAT({key}, '{salt}'))), 1000000) / 1000000)"


def _arr(options: list[str]) -> str:
    return "[" + ", ".join("'" + o.replace("'", "\\'") + "'" for o in options) + "]"


def pick(options: list[str], key: str, salt: str) -> str:
    return f"{_arr(options)}[OFFSET(CAST(FLOOR({rnd(key, salt)} * {len(options)}) AS INT64))]"


def _days() -> str:
    return f"""
  SELECT
    fecha,
    DATE_DIFF(fecha, DATE_SUB(CURRENT_DATE(), INTERVAL {DAYS} DAY), DAY) AS n,
    EXTRACT(DAYOFWEEK FROM fecha) IN (1, 7) AS finde,
    fecha = DATE_SUB(CURRENT_DATE(), INTERVAL {SPIKE_DAYS_AGO} DAY) AS pico
  FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(CURRENT_DATE(), INTERVAL {DAYS} DAY), DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))) AS fecha"""


def _hour(key: str) -> str:
    return f"""CASE
      WHEN {rnd(key, 'hor')} < 0.30 THEN 9 + CAST(FLOOR({rnd(key, 'h2')} * 4) AS INT64)
      WHEN {rnd(key, 'hor')} < 0.55 THEN 13 + CAST(FLOOR({rnd(key, 'h2')} * 3) AS INT64)
      WHEN {rnd(key, 'hor')} < 0.90 THEN 19 + CAST(FLOOR({rnd(key, 'h2')} * 5) AS INT64)
      ELSE CAST(FLOOR({rnd(key, 'h2')} * 24) AS INT64)
    END"""


# --- consultas de cada tabla -----------------------------------------------------------------


def select_productos() -> str:
    key = "CAST(i AS STRING)"
    cat = pick(CATEGORIES, key, "cat")
    noun = "CASE categoria " + " ".join(f"WHEN '{c}' THEN {pick(n, key, 'sus')}" for c, n in NOUNS.items()) + " END"
    base = "CASE categoria " + " ".join(f"WHEN '{c}' THEN {lo} + {rnd(key, 'pre')} * {hi - lo}" for c, (lo, hi) in PRICES.items()) + " END"
    return f"""
WITH p AS (
  SELECT i, {cat} AS categoria FROM UNNEST(GENERATE_ARRAY(1, {PRODUCTS})) AS i
)
SELECT
  CONCAT('pr', CAST(i AS STRING)) AS producto_id,
  CONCAT({noun}, ' ', {pick(MODELS, key, 'mod')}) AS nombre,
  categoria,
  {pick(BRANDS, key, 'mar')} AS marca,
  ROUND(FLOOR({base}) + 0.99, 2) AS precio_usd,
  ROUND((FLOOR({base}) + 0.99) * (0.45 + {rnd(key, 'cos')} * 0.15), 2) AS costo_usd,
  {rnd(key, 'act')} > 0.06 AS activo
FROM p"""


def select_clientes() -> str:
    key = "CAST(i AS STRING)"
    return f"""
SELECT
  CONCAT('c', CAST(i AS STRING)) AS cliente_id,
  DATE_SUB(CURRENT_DATE(), INTERVAL CAST(FLOOR({rnd(key, 'alt')} * 730) AS INT64) DAY) AS alta,
  {pick(COUNTRIES, key, 'pai')} AS pais,
  {pick(['Orgánico', 'Búsqueda paga', 'Redes sociales', 'Email', 'Referidos'], key, 'can')} AS canal_adquisicion,
  CASE
    WHEN {rnd(key, 'seg')} < 0.05 THEN 'VIP'
    WHEN {rnd(key, 'seg')} < 0.40 THEN 'Recurrente'
    ELSE 'Nuevo'
  END AS segmento
FROM UNNEST(GENERATE_ARRAY(0, {CUSTOMERS - 1})) AS i"""


def select_ordenes() -> str:
    """Atributos de cada pedido. La usan items_pedido y pedidos: dan lo mismo en las dos."""
    k = "okey"
    return f"""
WITH dias AS ({_days()}
),
o AS (
  SELECT d.fecha, d.finde, d.pico, CONCAT(FORMAT_DATE('%Y%m%d', d.fecha), '-', CAST(x AS STRING)) AS okey
  FROM dias AS d,
  UNNEST(GENERATE_ARRAY(1, CAST({BASE_ORDERS} * (1 + 0.003 * d.n) * IF(d.finde, 0.85, 1) * IF(d.pico, 3.1, 1) AS INT64))) AS x
),
o2 AS (
  SELECT
    *,
    CASE
      WHEN {rnd(k, 'can')} < IF(finde, 0.42, 0.30) THEN 'App'
      WHEN {rnd(k, 'can')} < 0.86 THEN 'Web'
      ELSE 'Marketplace'
    END AS canal,
    CASE
      WHEN pico AND {rnd(k, 'cup')} < 0.70 THEN 'CYBER30'
      WHEN {rnd(k, 'cup')} < 0.12 THEN {pick(['BIENVENIDA10', 'ENVIOGRATIS', 'VUELVE15'], k, 'cu2')}
    END AS cupon
  FROM o
)
SELECT
  fecha,
  okey,
  CONCAT('ped', REPLACE(okey, '-', '')) AS pedido_id,
  {_hour(k)} AS hora,
  CONCAT('c', CAST(CAST(FLOOR(POW({rnd(k, 'cli')}, 1.6) * {CUSTOMERS}) AS INT64) AS STRING)) AS cliente_id,
  canal,
  CASE WHEN canal = 'App' THEN 'mobile' WHEN {rnd(k, 'dis')} < 0.58 THEN 'mobile' ELSE 'desktop' END AS dispositivo,
  cupon,
  CASE cupon WHEN 'CYBER30' THEN 0.30 WHEN 'BIENVENIDA10' THEN 0.10 WHEN 'VUELVE15' THEN 0.15 ELSE 0 END AS descuento_pct,
  IF({rnd(k, 'est')} < 0.06, 'Cancelado', 'Entregado') AS estado,
  1 + CAST(FLOOR(POW({rnd(k, 'nit')}, 1.8) * 4) AS INT64) AS n_items
FROM o2"""


def select_items(productos: str) -> str:
    k = "ikey"
    return f"""
WITH ordenes AS ({select_ordenes()}
),
lineas AS (
  SELECT o.*, linea, CONCAT(o.okey, '-', CAST(linea AS STRING)) AS ikey
  FROM ordenes AS o, UNNEST(GENERATE_ARRAY(1, o.n_items)) AS linea
)
SELECT
  l.fecha,
  l.pedido_id,
  l.linea,
  pr.producto_id,
  IF({rnd(k, 'cnt')} < 0.15, 2, 1) AS cantidad,
  pr.precio_usd AS precio_unitario_usd,
  ROUND(pr.precio_usd * IF({rnd(k, 'cnt')} < 0.15, 2, 1) * l.descuento_pct, 2) AS descuento_usd,
  l.estado <> 'Cancelado' AND {rnd(k, 'dev')} < IF(pr.categoria = 'Calzado', 0.18, 0.04) AS devuelto
FROM lineas AS l
JOIN {productos} AS pr
  ON pr.producto_id = CONCAT('pr', CAST(1 + CAST(FLOOR(POW({rnd(k, 'pro')}, 2) * {PRODUCTS}) AS INT64) AS STRING))"""


def select_pedidos(items: str, clientes: str) -> str:
    return f"""
WITH ordenes AS ({select_ordenes()}
),
totales AS (
  SELECT
    pedido_id,
    SUM(cantidad) AS unidades,
    ROUND(SUM(precio_unitario_usd * cantidad - descuento_usd), 2) AS total_usd
  FROM {items}
  GROUP BY pedido_id
),
base AS (
  SELECT
    o.fecha,
    o.hora,
    o.pedido_id,
    o.cliente_id,
    c.pais,
    o.canal,
    o.dispositivo,
    o.cupon,
    t.unidades,
    t.total_usd,
    o.estado
  FROM ordenes AS o
  JOIN totales AS t USING (pedido_id)
  JOIN {clientes} AS c USING (cliente_id)
)
SELECT * FROM base
UNION ALL
-- Error de carga a propósito: ese día los pedidos se cargaron dos veces.
SELECT * FROM base WHERE fecha = DATE_SUB(CURRENT_DATE(), INTERVAL {DUPLICATED_DAYS_AGO} DAY)"""


def select_eventos() -> str:
    s = "skey"
    return f"""
WITH dias AS ({_days()}
),
sesiones AS (
  SELECT d.fecha, d.pico, CONCAT(FORMAT_DATE('%Y%m%d', d.fecha), '-', CAST(x AS STRING)) AS skey
  FROM dias AS d,
  UNNEST(GENERATE_ARRAY(1, CAST({BASE_SESSIONS} * (1 + 0.003 * d.n) * IF(d.finde, 1.1, 1) * IF(d.pico, 2.5, 1) AS INT64))) AS x
),
ses AS (
  SELECT
    fecha,
    {_hour(s)} AS hora,
    CONCAT('w', REPLACE({s}, '-', '')) AS sesion_id,
    CONCAT('v', CAST(MOD(ABS(FARM_FINGERPRINT(CONCAT({s}, 'vis'))), 60000) AS STRING)) AS visitante_id,
    {rnd(s, 'bot')} < 0.08 AS es_bot,
    pico,
    {s} AS skey
  FROM sesiones
),
ses2 AS (
  SELECT
    *,
    IF(es_bot, 'desktop', IF({rnd(s, 'dis')} < 0.68, 'mobile', 'desktop')) AS dispositivo,
    CASE
      WHEN es_bot THEN 'Directo'
      WHEN pico AND {rnd(s, 'fue')} < 0.40 THEN 'Email'
      ELSE {pick(['Orgánico', 'Orgánico', 'Búsqueda paga', 'Redes sociales', 'Directo', 'Email'], s, 'fu2')}
    END AS fuente,
    IF(es_bot, 10, 1 + CAST(FLOOR(POW({rnd(s, 'nv')}, 1.5) * 5) AS INT64)) AS n_vistas,
    NOT es_bot AND {rnd(s, 'car')} < IF(pico, 0.18, 0.10) AS carrito,
    {rnd(s, 'chk')} < 0.55 AS r_checkout,
    {rnd(s, 'com')} < IF(pico, 0.75, 0.62) AS r_compra,
    CONCAT('pr', CAST(1 + CAST(FLOOR(POW({rnd(s, 'pro')}, 2) * {PRODUCTS}) AS INT64) AS STRING)) AS producto_carrito
  FROM ses
),
eventos AS (
  SELECT fecha, hora, sesion_id, visitante_id, 'vista_producto' AS evento,
    CONCAT('pr', CAST(1 + CAST(FLOOR(POW({rnd(f"CONCAT({s}, CAST(v AS STRING))", 'vpr')}, 2) * {PRODUCTS}) AS INT64) AS STRING)) AS producto_id,
    dispositivo, fuente, es_bot
  FROM ses2, UNNEST(GENERATE_ARRAY(1, n_vistas)) AS v
  UNION ALL
  SELECT fecha, hora, sesion_id, visitante_id, 'agregar_carrito', producto_carrito, dispositivo, fuente, es_bot
  FROM ses2 WHERE carrito
  UNION ALL
  SELECT fecha, hora, sesion_id, visitante_id, 'checkout', NULL, dispositivo, fuente, es_bot
  FROM ses2 WHERE carrito AND r_checkout
  UNION ALL
  SELECT fecha, hora, sesion_id, visitante_id, 'compra', NULL, dispositivo, fuente, es_bot
  FROM ses2 WHERE carrito AND r_checkout AND r_compra
)
SELECT * FROM eventos"""


def select_ventas(items: str, pedidos: str, productos: str) -> str:
    return f"""
SELECT
  i.fecha,
  p.canal,
  pr.categoria,
  COUNT(DISTINCT i.pedido_id) AS pedidos,
  SUM(i.cantidad) AS unidades,
  ROUND(SUM(i.precio_unitario_usd * i.cantidad - i.descuento_usd), 2) AS ingresos_brutos_usd,
  ROUND(SUM(IF(i.devuelto, i.precio_unitario_usd * i.cantidad - i.descuento_usd, 0)), 2) AS devoluciones_usd,
  ROUND(SUM(IF(i.devuelto, 0, i.precio_unitario_usd * i.cantidad - i.descuento_usd)), 2) AS ingresos_netos_usd
FROM {items} AS i
JOIN (SELECT DISTINCT pedido_id, canal, estado FROM {pedidos}) AS p USING (pedido_id)
JOIN {productos} AS pr USING (producto_id)
WHERE p.estado <> 'Cancelado'
GROUP BY i.fecha, p.canal, pr.categoria"""


# --- script completo ------------------------------------------------------------------------


def _q(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _cols(cols: list[tuple[str, str, str]]) -> str:
    return ",\n".join(f"  {name} {typ} OPTIONS(description={_q(desc)})" for name, typ, desc in cols)


PRODUCTOS_COLS = [
    ("producto_id", "STRING", "Id del producto (pr1, pr2, …). Se cruza con items_pedido y eventos_web."),
    ("nombre", "STRING", "Nombre del producto (generado)."),
    ("categoria", "STRING", "Camperas, Calzado, Mochilas, Carpas, Ropa térmica o Accesorios."),
    ("marca", "STRING", "Marca (todas ficticias)."),
    ("precio_usd", "FLOAT64", "Precio de lista, en dólares."),
    ("costo_usd", "FLOAT64", "Costo unitario, en dólares."),
    ("activo", "BOOL", "FALSE si el producto ya no se vende."),
]
CLIENTES_COLS = [
    ("cliente_id", "STRING", "Id del cliente. Se cruza con pedidos.cliente_id."),
    ("alta", "DATE", "Día en que se registró."),
    ("pais", "STRING", "País del cliente."),
    ("canal_adquisicion", "STRING", "Por dónde llegó: Orgánico, Búsqueda paga, Redes sociales, Email o Referidos."),
    ("segmento", "STRING", "Nuevo, Recurrente o VIP."),
]
PEDIDOS_COLS = [
    ("fecha", "DATE", "Día del pedido. Partición: filtrala siempre."),
    ("hora", "INT64", "Hora local (0 a 23)."),
    ("pedido_id", "STRING", "Id del pedido. Se cruza con items_pedido.pedido_id."),
    ("cliente_id", "STRING", "Cliente. Se cruza con clientes.cliente_id."),
    ("pais", "STRING", "País del cliente."),
    ("canal", "STRING", "Web, App o Marketplace."),
    ("dispositivo", "STRING", "mobile o desktop."),
    ("cupon", "STRING", "Cupón usado; NULL si no hubo."),
    ("unidades", "INT64", "Unidades del pedido."),
    ("total_usd", "FLOAT64", "Total cobrado, con descuentos y sin restar devoluciones."),
    ("estado", "STRING", "Entregado o Cancelado."),
]
ITEMS_COLS = [
    ("fecha", "DATE", "Día del pedido. Partición: filtrala siempre."),
    ("pedido_id", "STRING", "Pedido al que pertenece la línea."),
    ("linea", "INT64", "Número de línea dentro del pedido."),
    ("producto_id", "STRING", "Producto. Se cruza con productos.producto_id."),
    ("cantidad", "INT64", "Unidades de la línea."),
    ("precio_unitario_usd", "FLOAT64", "Precio unitario al momento de la compra."),
    ("descuento_usd", "FLOAT64", "Descuento de la línea por cupón."),
    ("devuelto", "BOOL", "TRUE si el cliente devolvió la línea."),
]
EVENTOS_COLS = [
    ("fecha", "DATE", "Día del evento. Partición: filtrala siempre."),
    ("hora", "INT64", "Hora local (0 a 23)."),
    ("sesion_id", "STRING", "Sesión del sitio o la app."),
    ("visitante_id", "STRING", "Navegador o dispositivo anónimo."),
    ("evento", "STRING", "vista_producto, agregar_carrito, checkout o compra."),
    ("producto_id", "STRING", "Producto visto o agregado; NULL en checkout y compra."),
    ("dispositivo", "STRING", "mobile o desktop."),
    ("fuente", "STRING", "Orgánico, Búsqueda paga, Redes sociales, Directo o Email."),
    ("es_bot", "BOOL", "TRUE si la sesión es de un bot."),
]
VENTAS_COLS = [
    ("fecha", "DATE", "Día."),
    ("canal", "STRING", "Web, App o Marketplace."),
    ("categoria", "STRING", "Categoría del producto."),
    ("pedidos", "INT64", "Pedidos distintos, sin cancelados."),
    ("unidades", "INT64", "Unidades vendidas."),
    ("ingresos_brutos_usd", "FLOAT64", "Ventas con descuentos, antes de devoluciones."),
    ("devoluciones_usd", "FLOAT64", "Lo devuelto."),
    ("ingresos_netos_usd", "FLOAT64", "Ingresos brutos menos devoluciones."),
]


def script(project: str, dataset: str) -> str:
    ds = f"`{project}.{dataset}`"
    t = lambda name: f"`{project}.{dataset}.{name}`"  # noqa: E731
    return f"""-- Demo de agent-dashboards: "Andes Outdoor", tienda online ficticia. Datos sintéticos.
-- Generado por agent_dashboards/demo/seed.py. Se puede volver a correr: reemplaza las tablas.

CREATE SCHEMA IF NOT EXISTS {ds}
OPTIONS (description = 'Demo: Andes Outdoor, una tienda online ficticia de equipo outdoor, con datos sintéticos de los últimos {DAYS} días.');

CREATE OR REPLACE TABLE {t('productos')} (
{_cols(PRODUCTOS_COLS)}
)
OPTIONS (description = 'Catálogo de productos. Una fila por producto.')
AS {select_productos()};

CREATE OR REPLACE TABLE {t('clientes')} (
{_cols(CLIENTES_COLS)}
)
OPTIONS (description = 'Clientes registrados. Una fila por cliente.')
AS {select_clientes()};

CREATE OR REPLACE TABLE {t('items_pedido')} (
{_cols(ITEMS_COLS)}
)
PARTITION BY fecha
CLUSTER BY producto_id
OPTIONS (description = 'Una fila por línea de pedido, con descuento y devolución. Incluye las líneas de pedidos cancelados.')
AS {select_items(t('productos'))};

CREATE OR REPLACE TABLE {t('pedidos')} (
{_cols(PEDIDOS_COLS)}
)
PARTITION BY fecha
CLUSTER BY canal
OPTIONS (description = 'Una fila por pedido. Incluye cancelados: excluilos para medir ventas.')
AS {select_pedidos(t('items_pedido'), t('clientes'))};

CREATE OR REPLACE TABLE {t('eventos_web')} (
{_cols(EVENTOS_COLS)}
)
PARTITION BY fecha
CLUSTER BY evento
OPTIONS (description = 'Eventos del embudo de compra en el sitio y la app. Incluye bots (es_bot).')
AS {select_eventos()};

CREATE OR REPLACE TABLE {t('ventas_diarias')} (
{_cols(VENTAS_COLS)}
)
PARTITION BY fecha
OPTIONS (description = 'Ventas agregadas por día, canal y categoría, sin cancelados. Más barata que pedidos para tendencias.')
AS {select_ventas(t('items_pedido'), t('pedidos'), t('productos'))};
"""


def check_queries() -> list[tuple[str, str]]:
    """Cada SELECT con sus dependencias como CTE, para validarlo con un dry run sin crear nada."""
    prod = f"productos AS ({select_productos()})"
    cli = f"clientes AS ({select_clientes()})"
    items = f"items_pedido AS ({select_items('productos')})"
    ped = f"pedidos AS ({select_pedidos('items_pedido', 'clientes')})"
    return [
        ("productos", select_productos()),
        ("clientes", select_clientes()),
        ("items_pedido", f"WITH {prod}\nSELECT * FROM ({select_items('productos')})"),
        ("pedidos", f"WITH {prod}, {cli}, {items}\nSELECT * FROM ({select_pedidos('items_pedido', 'clientes')})"),
        ("eventos_web", select_eventos()),
        ("ventas_diarias", f"WITH {prod}, {cli}, {items}, {ped}\nSELECT * FROM ({select_ventas('items_pedido', 'pedidos', 'productos')})"),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea el dataset de demo (Andes Outdoor) en BigQuery.")
    parser.add_argument("--project", required=True, help="Proyecto de Google Cloud donde crear el dataset")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help=f"Nombre del dataset (default {DEFAULT_DATASET})")
    parser.add_argument("--location", default="US", help="Ubicación del dataset (default US)")
    parser.add_argument("--run", action="store_true", help="Correrlo con `bq` en vez de imprimir el SQL")
    args = parser.parse_args()
    sql = script(args.project, args.dataset)
    if not args.run:
        sys.stdout.write(sql)
        return
    bq = shutil.which("bq")
    if not bq:
        sys.exit("No encontré `bq` (viene con el SDK de Google Cloud). Corré sin --run y pegá el SQL en la consola de BigQuery.")
    cmd = [bq, f"--project_id={args.project}", f"--location={args.location}", "query", "--use_legacy_sql=false", "--nouse_cache"]
    print(f"Creando {args.project}.{args.dataset}… (tarda uno o dos minutos)")
    # bq es Python: sin esto, en Windows lee el SQL con la codificación de la consola y los
    # textos con acento ("Ropa térmica", las descripciones) quedan rotos en BigQuery.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(cmd, input=sql, text=True, encoding="utf-8", env=env)
    if result.returncode:
        sys.exit(result.returncode)
    print(f"Listo. Sumá `{args.project}.{args.dataset}` a WAREHOUSE_DATASETS y `{args.project}` a WAREHOUSE_PROJECT.")


if __name__ == "__main__":
    main()
