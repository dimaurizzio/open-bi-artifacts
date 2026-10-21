# Diseño

Criterios para lo que la persona no especificó. Las reglas del almacén y la API
`dash` están en `SKILL.md`.

## Qué mostrar

- Un dashboard sirve más cuando responde a una decisión y su conclusión lo dice.
- Arriba, un título de una línea (el de `dash/meta` o parecido) y una oración con
  la conclusión, llenada en `onData`. Más de seis números grandes en fila cuesta leer.
- El primer número se lee como el más importante: empezá por el más cercano al
  resultado (ventas antes que visitas).
- Un número clave dice más con su variación contra el período anterior: con signo,
  el período nombrado y el color según si es bueno o malo.
- Etapas de un mismo proceso se entienden mejor como embudo o como tasa que como
  tarjetas sueltas.
- Cada tarjeta dice su ventana de tiempo ("Últimos 28 días").
- Una tabla de apertura conserva las columnas de la tabla madre, en el mismo orden.
- Definí cada métrica en palabras simples donde aparece por primera vez; si el
  método lleva más de una oración, puede tener su propia sección.
- Si hay una definición acordada de la métrica (en el conocimiento guardado),
  usala; si el SQL es tuyo, decilo junto a la definición.
- Sumá una apertura que probablemente pidan después (por canal, por categoría).
- Cada consulta en vivo corre de nuevo para cada lector: si una tabla más chica o
  ya agregada tiene la respuesta, es mejor fuente.
- Si dejaste algo afuera, decilo en la página con el motivo.
- Con varias preguntas, una pestaña por pregunta funciona mejor que una página
  larga; otras aperturas de los mismos datos pueden ir detrás de un filtro.

## Formatos posibles

Para combinar o ignorar; si un formato dice otra cosa que las reglas de arriba,
gana el formato.

- **Monitoreo**: para tener abierto. Una línea de estado con lo que está fuera de
  rango ahora, la misma métrica para cada miembro en gráficos chicos con una
  escala compartida, y los incidentes recientes en una tabla. Solo lo que se pasa
  de un límite va en color de alerta; nada se mueve.
- **Tablero semanal**: cuatro a seis tarjetas, cada una con el último valor grande,
  su variación, una tendencia chica y su meta. Solo una meta incumplida va en rojo.
- **Experimento**: el efecto sobre la métrica principal como número grande, una
  oración sobre si su intervalo del 95% cruza el cero, el intervalo de cada
  métrica contra una línea en cero, y la tasa por grupo en el tiempo.
- **Tabla**: muchas filas para recorrer. Una tabla que ordena y busca, barras
  dentro de las celdas con una escala compartida, una mini tendencia por fila, y
  columnas secundarias que se ocultan cuando hay poco ancho.
- **Embudo**: dónde se pierde la gente. Un embudo de verdad: etapas centradas y
  proporcionales unidas por trapecios, la tasa de paso entre etapas y la mayor
  pérdida en color de alerta (receta `embudo` en `references/graficos.md`).
- **Resumen de un período**: unos pocos totales arriba, un gráfico grande con los
  eventos fechados marcados, y la misma medida por grupo en gráficos chicos con
  una escala.
- **Análisis**: un hallazgo para leer de arriba abajo. El hallazgo en una oración
  alrededor de un número, y después un gráfico bajo cada subtítulo que dice lo
  que muestra; una serie destacada y el resto apagadas.
- **Historia con scroll**: capítulos cortos de texto en una columna al lado de un
  gráfico que queda fijo (`position: sticky`) y cambia cuando cada capítulo llega
  al medio de la pantalla (un `IntersectionObserver` con `root: document`; sin eso
  la página ignora su `rootMargin`). Una idea por capítulo, con sus números
  marcados como cualquier otro. Con poco ancho, el gráfico queda arriba y el texto
  pasa por debajo.

## Estilo

- La plataforma le da a la página tipografía por defecto y su margen exterior, que
  crece con el ancho: el contenedor más externo de la página no lleva padding
  propio. Para cambiar el margen, el CSS de la página setea
  `#dash-root { --dash-gutter: … }` (`0` va de borde a borde); es la única regla
  sobre `#dash-root`. Para un ancho máximo, un contenedor con `max-width` y
  `margin-inline: auto`.
- **Tarjetas** para gráficos y tablas, cada una con su título y una nota de una
  línea arriba: `background: var(--color-panel)`,
  `border: 1px solid var(--color-border-line)`, `border-radius: var(--ds-radius)`,
  `padding: var(--ds-pad-lg)`. Una grilla
  (`repeat(auto-fit, minmax(min(100%, 20rem), 1fr))` con `gap: var(--ds-gap-md)`)
  pone dos por fila cuando hay lugar.
- **Texto**: `font-family: var(--font-sans)`, en oración (no Title Case). Tamaños:
  `--ds-font-size-caption`, `-body`, `-heading`, `-title` y `-display`;
  `heading` con `--ds-font-weight-medium` para títulos de gráficos y secciones.
  `font-variant-numeric: tabular-nums` alinea los números.
- **Números grandes**: un título de página o el número principal de una tarjeta
  queda bien con `var(--font-serif)` en peso 400, a `--ds-font-size-title` o más.
- **Espacios**: `--ds-gap-` y `--ds-pad-` con `xs`, `sm`, `md`, `lg` o `xl`, y
  `--ds-radius` (o `--ds-radius-sm`) para las esquinas. Las cajas de una fila
  quedan mejor con la misma altura.
- **Colores**: las series usan `dash.colors` en orden. Si una importa más, el
  resto puede ir en `--ds-chart-muted`; un valor fuera de límite, en
  `--ds-chart-status-critical` (también hay `-serious`, `-warning` y `-good`).
  Para texto, `--color-fg`, `--color-fg-muted`, `--color-ok`, `--color-warn` y
  `--color-bad`. Todos siguen el tema: no hace falta una regla para modo oscuro.
- **Gráficos**: partí de las recetas de `references/graficos.md`. Conteos por día,
  semana o mes, en barras (`d3.scaleBand` con espacio); una tasa, un acumulado o
  varias series en el tiempo, en línea recta de punto a punto (una curva sugiere
  valores que no existen). Líneas de 2 px con extremos redondeados; barras de hasta
  32 px con solo el extremo libre redondeado (`rx` 4): en barras apiladas, la punta
  de la columna y nunca cada segmento, con 1 px de aire entre segmentos. Grilla de 1 px en `--ds-chart-grid`, eje en `--ds-chart-axis`, línea
  de referencia en `--ds-chart-reference` y etiquetas en `--color-fg-muted` a
  tamaño caption. Una línea etiquetada al final con su nombre y último valor
  reemplaza a la leyenda.
- **Tooltip**: `background: var(--ds-surface-popover)`,
  `border: 1px solid var(--ds-border)`, `border-radius: var(--ds-radius)`,
  `box-shadow: var(--ds-shadow-popover)`,
  `padding: var(--ds-pad-xs) var(--ds-pad-sm)`, tamaño caption y
  `pointer-events: none`. Primero la fecha o categoría en `--ds-text-secondary`,
  después una línea por serie con su color, nombre y valor. Va
  `position: absolute` dentro de una caja `position: relative`. El punto o la
  barra bajo el mouse se resalta.
- **Tablas largas**: en una caja con `max-height` y `overflow: auto`, con los `th`
  en `position: sticky; top: 0`. Encabezados que ordenan (`aria-sort`, números
  como números) y un `<input>` que busca por texto, todo sobre las filas que ya
  tiene la página. Una columna de clave formateada guarda su valor original en
  `data-key` del `<tr>`.
- **Formatos**: conteos con `d3.format(',')`, proporciones (0 a 1) con
  `d3.format('.1%')`, números grandes desde 10.000 con
  `Intl.NumberFormat('es', {notation: 'compact', maximumFractionDigits: 1})`,
  fechas con `d3.utcFormat('%d/%m')` sobre `new Date(fecha)`.

## Estados

La plataforma marca qué está pasando con los datos y la página lo estiliza:

- **Fuente que falló**: una línea centrada en su elemento
  (`display: flex; align-items: center; justify-content: center`) con un punto de
  0,375rem en `var(--color-bad)` y el texto "No cargó" en `--color-fg-muted` a
  tamaño caption. El elemento mantiene su `min-height` para que nada se mueva, y
  el mensaje del conector no va en la página (está en Fuentes). Se dibuja donde
  `dash.data(id).status === "error"`; una fuente que espera algo de quien la abre
  (un permiso) también queda marcada como error, con estado `connect`.
- **Valor vacío**: `[data-dash-source-state=empty] { color: transparent; }`, sin
  texto de "Sin datos". Si un gráfico entero vacío parecería roto, una línea
  apagada bajo su título explica por qué.
- **Cargando**: los elementos que van a mostrar datos llevan la clase
  `dash-skeleton` (una barra redondeada en lugar del texto, o la caja entera de un
  gráfico) hasta que llegan los datos o falla la carga. La caja de un gráfico va
  vacía, sin `—`.
- **Refrescando**: `[data-dash-source-state=refreshing] { opacity: .5; transition: opacity var(--ds-dur-slow); }`
  muestra qué se está actualizando sin esconder los valores anteriores.
- La caja de un gráfico con `min-height` igual a su altura dibujada evita que un
  cambio de estado mueva lo de abajo.

## Filtros

Un valor por defecto que depende del día (por ejemplo, una fecha relativa a hoy)
se calcula en el código de la página y se setea con `dash.setParams` arriba del
script: las consultas ya arrancan con él. El botón de reinicio llama a ese mismo
código después de `dash.resetParams()`, que vuelve a `dash/params`.

```js
const porDefecto = () => dash.setParams({hasta: d3.utcFormat('%Y-%m-%d')(d3.utcDay.offset(new Date(), -1))});
porDefecto();
document.querySelector('.fx-reiniciar').addEventListener('click', () => { dash.resetParams(); porDefecto(); });
```

## Links

Un link nombra un lugar de la página: una pestaña, una sección o cualquier
elemento con `id`. La página lo muestra sola una vez dibujada, ignora nombres que
no son suyos, y actualiza el link a medida que la persona se mueve.

```js
const ir = (el) => { el.scrollIntoView(); dash.setLink(el.id); }; // en una pestaña, mostrarla
const nombrado = document.getElementById(location.hash.slice(1));
if (nombrado) ir(nombrado); // después del primer dibujo, cuando el elemento existe
```

## d3

- El global `d3` es casi todo d3 v7: escalas, formas, ejes, formatos, jerarquías,
  redes, acordes, contornos, zoom, brush y transiciones. No trae mapas, esquemas de
  color fijos ni lectura de CSV: las filas salen de `dash.data`, y una escala de
  color se arma entre dos tokens con `d3.interpolateRgb` sobre su color computado.
- `d3.scaleUtc` o `d3.scaleLinear` con un solo argumento setean el rango, no el
  dominio: pasá los dos.
- Convertí cada fecha a `Date` una vez, antes de escalar: un `NaN` o
  `Invalid Date` en la página es una fecha sin convertir.
- `d3.axis` pone `font-size` 10 y `font-family` sans-serif en su grupo:
  `.attr('font-size', null).attr('font-family', null)` los saca.
- Un `<b>` sale en negrita 700 por una regla de la plataforma
  (`[data-dash-content] :is(b, strong)`): para otro peso, usá un selector al menos
  igual de específico (`.mi-pagina b`).
- Los nombres de token van completos: `var(--ds-chart-${x})` esconde un error.
- Un área bajo una línea puede desvanecerse hacia la base con un `linearGradient`
  vertical (`x2` 0, `y2` 1) con paradas en 0, .25, .5, .75 y 1 a opacidad .26,
  .16, .08, .03 y 0, coloreadas con `style` (`stop-color: ${dash.colors[0]}`),
  porque `d3.color()` no lee `dash.colors`.
- Las líneas de referencia punteadas dejan los extremos planos (`butt`) para que
  los trazos queden parejos.

## Antes de escribir

Revisá los archivos: cada `var(--ds-…)` escrito completo y de esta lista; cada
número y fecha con formato; cada gráfico mide sus márgenes desde la etiqueta más
ancha tal como se dibuja (`getComputedTextLength()`) y evita que las etiquetas se
pisen (menos marcas, etiquetas finales separadas). La animación va con
`@keyframes`, `--ds-dur-slow` y `--ds-ease-out`, dentro de
`@media (prefers-reduced-motion: no-preference)`, y arranca con el primer dibujo
que tiene filas para que un redibujo no la repita.

## Entrega

Contale a la persona, una línea por cosa, qué puede hacer: pedirte cualquier
gráfico o control por nombre (un filtro de fecha, un click que filtra todo, un
diagrama), bajar las filas de una fuente en CSV desde Fuentes y, si es editora,
editar el SQL de una consulta en Fuentes, probarlo, guardarlo y volver a una
versión anterior. Después, lo que tiene que saber de los datos: cada consulta en
vivo corre con el acceso de quien abre el dashboard, cada vez que lo abre,
refresca o filtra; y una consulta que no se refresca sola muestra a todos sus
resultados guardados, sin importar su acceso, hasta que te pidan refrescarla o un
editor la vuelva a poner en vivo.
