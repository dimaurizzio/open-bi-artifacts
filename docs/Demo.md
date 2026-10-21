# Demo: Andes Outdoor, una tienda online ficticia

Para probar la plataforma o grabar demos sin datos reales. `demo/seed.py` crea en
tu proyecto de BigQuery un dataset con datos sintéticos de una tienda online de
equipo outdoor, y `demo/knowledge.py` carga lo que "el equipo" ya sabe de esos datos.

| Tabla | Qué es | Filas aprox. |
| --- | --- | --- |
| `productos` | Catálogo: categoría, marca, precio, costo | 300 |
| `clientes` | Alta, país, canal de adquisición, segmento | 25.000 |
| `pedidos` | Una fila por pedido, particionada por día | 55.000 |
| `items_pedido` | Una fila por línea: producto, cantidad, descuento, devolución | 110.000 |
| `eventos_web` | Embudo: vista de producto, carrito, checkout, compra | 1.300.000 |
| `ventas_diarias` | Agregado por día, canal y categoría, sin cancelados | 1.000 |

Los datos cubren los últimos 56 días y traen cosas para descubrir en vivo:

- **Estacionalidad:** los fines de semana hay menos pedidos y más compras desde la app.
- **Un Cyber Day** hace 9 días: cupón `CYBER30`, más tráfico por email y el triple de pedidos.
- **Bots** en los eventos web (`es_bot`): solo ven productos, nunca compran.
- **Devoluciones:** Calzado devuelve cerca del 17% (por talle); el resto, un 4%.
- **Un error de carga** hace 23 días: ese día los pedidos están duplicados en `pedidos`,
  pero no en `items_pedido`. Las dos tablas no cierran ese día.
- **Cancelados:** cerca del 6% de los pedidos, que no cuentan como ventas.

Las tablas y columnas tienen descripciones en BigQuery, así que el catálogo de la
app las muestra apenas se sincroniza.

## Cuesta $0

Alcanza con el **sandbox de BigQuery**: no hace falta cuenta de facturación. El
sandbox borra las tablas y particiones de más de 60 días, por eso los datos son
de los últimos 56: para refrescar la demo, se vuelve a correr el seed (reemplaza
las tablas y las deja al día). Las consultas de la demo escanean pocos MB, muy por
debajo del TB mensual gratuito.

## Paso a paso

1. **Proyecto.** En https://console.cloud.google.com creá un proyecto (o usá uno
   tuyo). La API de BigQuery, y con ella el servidor MCP, viene habilitada en los
   proyectos nuevos.
2. **Permisos.** Quien crea el proyecto ya puede todo. Para que otras cuentas
   prueben la app con `QUERY_CREDENTIALS=reader`, dales en el proyecto *BigQuery
   Data Viewer*, *BigQuery Job User* y *MCP Tool User*.
3. **Crear el dataset.** Con el SDK de Google Cloud (`bq` viene con `gcloud`),
   logueado con una cuenta que tenga permisos en el proyecto (`gcloud auth login`),
   desde la carpeta de arriba del proyecto:

   ```bash
   python -m agent_dashboards.demo.seed --project TU_PROYECTO --run
   ```

   Sin `--run` imprime el SQL, para pegarlo en la consola de BigQuery.
4. **Configurar la app** en `.env.local` (o `.env.prod`):

   ```
   WAREHOUSE_PROJECT=TU_PROYECTO
   WAREHOUSE_DATASETS=TU_PROYECTO.demo_andes_outdoor
   ```

   Para el catálogo, la app usa las credenciales del servidor
   (`gcloud auth application-default login` en tu máquina).
5. **Levantar la app y entrar.** Al arrancar sincroniza el catálogo; si no, un
   admin lo sincroniza desde el panel Conocimiento.
6. **Cargar el conocimiento de demo** (la regla de excluir cancelados y bots, las
   advertencias del Cyber Day, del día duplicado y de las devoluciones, métricas y
   joins):

   ```bash
   python -m agent_dashboards.demo.knowledge --project TU_PROYECTO --curator vos@tudominio.com
   ```

   El curador tiene que estar en `ADMIN_EMAILS` o `KNOWLEDGE_CURATORS`. Si la app
   corre en Docker: `docker compose exec app python -m agent_dashboards.demo.knowledge …`.

Las advertencias nombran fechas relativas al día en que se cargan. Si volvés a
correr el seed otro día, las fechas se corren: deprecá esas entradas desde el
panel Conocimiento y volvé a cargar el conocimiento.

## Ideas para los videos

Cada una muestra una parte de la plataforma y dura uno o dos minutos.

1. **Una pregunta en lenguaje natural.** "¿Cuánto vendimos ayer por canal?" El agente
   consulta el conocimiento, excluye los cancelados sin que se lo pidas, muestra el
   SQL y cita la tabla y el período.
2. **Investigar una anomalía.** "¿Qué pasó hace 9 días que se dispararon los pedidos?"
   Encuentra el Cyber Day y el cupón. Seguí con "¿y por qué hace 23 días pedidos e
   ítems no cierran?": aparece la advertencia del día duplicado.
3. **Un dashboard en vivo.** "Armame un dashboard de ventas de las últimas 4 semanas
   por canal y categoría, con el embudo de conversión y un filtro de fecha." Mostrá el
   modo Datos (cada número abre su consulta) y Fuentes (SQL editable, versiones).
4. **Enseñarle algo.** "Ojo, los pedidos de Marketplace pagan una comisión del 12%:
   para el margen, restala." El agente arma un borrador, lo guarda con tu confirmación,
   y el conocimiento aparece en el panel con su autor, pendiente de aprobación.
5. **Equipo y permisos.** Creá un proyecto, sumá a alguien como lector, mové el
   dashboard al proyecto y compartí un chat. Entrando con la otra cuenta, el
   dashboard se ve pero no se edita.
6. **Despliegue.** `docker compose up -d` en un VPS con Cloudflare Tunnel, sin
   puertos abiertos (ver [despliegue.md](despliegue.md)).

Antes de grabar:

- Usá una cuenta de demo o tapá los mails: la barra lateral muestra tu cuenta.
- Si querés números estables entre tomas, grabá el mismo día en que corriste el
  seed: todo se calcula respecto de la fecha de hoy.
- Los datos son sintéticos: decilo en el video o en la descripción.
