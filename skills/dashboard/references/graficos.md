# Gráficos

Recetas probadas para los gráficos que más se piden, con d3 v7, `dash.colors` y los
tokens del visor: se ven bien en tema claro y oscuro. Partí de ellas y adaptalas a
lo que pidió la persona (otra medida, otra apertura, otro formato); para un gráfico
que no está acá, seguí el mismo estilo.

## Cuándo usar cada una

| Pregunta | Receta |
| --- | --- |
| Cómo evoluciona un total y de qué se compone (ventas por día y canal) | `barrasApiladas` |
| Dónde se pierde la gente entre etapas de un proceso | `embudo` |
| Cómo viene una medida contra el período anterior | `lineaComparada` |
| Quién aporta más (categorías, productos, países) | `ranking` |

Reglas que las recetas ya cumplen y que valen para cualquier gráfico:

- **Esquinas.** Solo el extremo libre de una barra va redondeado (`rx` 4). En una
  columna apilada se redondea la punta de la columna, nunca cada segmento; entre
  segmentos queda 1 px de aire.
- **Embudo de verdad.** Etapas centradas y proporcionales, unidas por trapecios,
  con la tasa de paso entre cada par y la mayor caída en `--color-bad`. No lo armes
  con barras sueltas.
- **Ancho.** Cada receta mide `el.clientWidth` y dibuja un `viewBox` de ese ancho:
  llamala dentro de `dash.onData`, que corre de nuevo cuando cambian los datos o el
  ancho. La caja del gráfico va con `position: relative` (por el tooltip) y un
  `min-height` igual a su alto.
- **Fechas.** Las filas llegan con fechas en texto: convertilas una vez con
  `new Date(fila.fecha)` antes de pasarlas.
- **Marcas.** El contenedor lleva `data-source` con las fuentes que dibuja, una sola
  vez y no en el `<svg>`.
- **Texto.** Todo texto va con `.text()`: nunca `.html()`.

## Uso

```js
dash.onData(() => {
  const v = dash.data('diario');
  if (v.status !== 'ok') return;
  const filas = v.data.map((r) => ({ fecha: new Date(r.fecha), serie: r.canal, valor: Number(r.ventas) }));
  barrasApiladas(document.getElementById('grafico-diario'), filas, {
    eventos: [{ fecha: new Date('2026-10-07'), texto: 'Cyber Day' }],
  });
});
```

```html
<section class="tarjeta">
  <h3>Ventas diarias por canal</h3>
  <p>USD por día, apiladas por canal</p>
  <div id="grafico-diario" class="grafico-caja" data-source="diario"></div>
</section>
```

`.grafico-caja { position: relative; min-height: 290px; }` en el CSS de la página.

## Código

Va en un archivo de script de la página (por ejemplo `files/graficos.js`, cargado
antes del script principal) y los estilos, en su CSS.

```js
// Recetas de gráficos para páginas de dashboards: d3 v7, tokens del visor, dash.colors.
// Cada una recibe el elemento contenedor (position: relative) y filas ya convertidas.

const compacto = new Intl.NumberFormat('es', { notation: 'compact', maximumFractionDigits: 1 });
const entero = new Intl.NumberFormat('es');
const pct1 = new Intl.NumberFormat('es', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 });
const diaMes = d3.utcFormat('%d/%m');

// Rectángulo con solo las esquinas de un lado redondeadas ('arriba' o 'derecha').
function barraRedondeada(x, y, w, h, r, lado) {
  r = Math.max(0, Math.min(r, w / 2, h));
  if (lado === 'derecha') {
    r = Math.min(r, h / 2, w);
    return `M${x},${y}h${w - r}a${r},${r} 0 0 1 ${r},${r}v${h - 2 * r}a${r},${r} 0 0 1 ${-r},${r}h${-(w - r)}z`;
  }
  return `M${x},${y + h}v${-(h - r)}a${r},${r} 0 0 1 ${r},${-r}h${w - 2 * r}a${r},${r} 0 0 1 ${r},${r}v${h - r}z`;
}

// Tooltip compartido: una caja HTML dentro del contenedor.
function tooltip(el) {
  const tip = d3.select(el).append('div').attr('class', 'grafico-tip').style('opacity', 0);
  return {
    mostrar(x, y, titulo, lineas) {
      tip.selectAll('*').remove();
      tip.append('div').attr('class', 'grafico-tip-titulo').text(titulo);
      lineas.forEach(([color, nombre, valor]) => {
        const l = tip.append('div').attr('class', 'grafico-tip-linea');
        l.append('span').attr('class', 'grafico-tip-punto').style('background', color);
        l.append('span').text(nombre);
        l.append('b').text(valor);
      });
      const caja = el.getBoundingClientRect();
      const ancho = tip.node().offsetWidth;
      tip.style('left', `${Math.min(Math.max(x - ancho / 2, 0), caja.width - ancho)}px`).style('top', `${Math.max(y - tip.node().offsetHeight - 10, 0)}px`).style('opacity', 1);
    },
    ocultar() { tip.style('opacity', 0); },
  };
}

function leyenda(el, items) {
  const ley = d3.select(el).append('div').attr('class', 'grafico-leyenda');
  items.forEach(([color, nombre]) => {
    const i = ley.append('span');
    i.append('span').attr('class', 'grafico-tip-punto').style('background', color);
    i.append('span').text(nombre);
  });
}

// 1. Barras apiladas en el tiempo: solo la punta de cada columna va redondeada.
// filas: [{fecha: Date, serie: 'Web', valor: 123}], eventos: [{fecha: Date, texto: 'Cyber Day'}]
function barrasApiladas(el, filas, { alto = 260, formato = compacto, eventos = [] } = {}) {
  el.textContent = '';
  const series = [...new Set(filas.map((d) => d.serie))];
  const color = new Map(series.map((s, i) => [s, dash.colors[i % dash.colors.length]]));
  const fechas = [...new Set(filas.map((d) => +d.fecha))].sort((a, b) => a - b).map((t) => new Date(t));
  const tabla = fechas.map((f) => {
    const fila = { fecha: f };
    series.forEach((s) => { fila[s] = d3.sum(filas, (d) => (+d.fecha === +f && d.serie === s ? d.valor : 0)); });
    return fila;
  });
  const capas = d3.stack().keys(series)(tabla);
  leyenda(el, series.map((s) => [color.get(s), s]));
  const W = el.clientWidth, m = { t: 22, r: 4, b: 22, l: 40 };
  const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${W} ${alto}`).attr('width', '100%').attr('class', 'grafico');
  const x = d3.scaleBand().domain(fechas.map((f) => +f)).range([m.l, W - m.r]).paddingInner(0.28).paddingOuter(0.1);
  const y = d3.scaleLinear().domain([0, d3.max(tabla, (d) => d3.sum(series, (s) => d[s]))]).nice().range([alto - m.b, m.t]);
  const ancho = Math.min(x.bandwidth(), 32), corrido = (x.bandwidth() - ancho) / 2;

  svg.append('g').selectAll('line').data(y.ticks(4)).join('line')
    .attr('x1', m.l).attr('x2', W - m.r).attr('y1', (d) => y(d)).attr('y2', (d) => y(d))
    .attr('stroke', 'var(--ds-chart-grid)');
  svg.append('g').attr('class', 'grafico-eje').selectAll('text').data(y.ticks(4)).join('text')
    .attr('x', m.l - 6).attr('y', (d) => y(d)).attr('dy', '0.32em').attr('text-anchor', 'end').text((d) => formato.format(d));
  const cada = Math.ceil(fechas.length / Math.max(2, Math.floor((W - m.l) / 56)));
  svg.append('g').attr('class', 'grafico-eje').selectAll('text').data(fechas.filter((_, i) => i % cada === 0)).join('text')
    .attr('x', (d) => x(+d) + x.bandwidth() / 2).attr('y', alto - 6).attr('text-anchor', 'middle').text((d) => diaMes(d));

  // La capa de más arriba con valor en cada columna lleva la punta redondeada.
  const tope = new Map(tabla.map((d) => [+d.fecha, [...series].reverse().find((s) => d[s] > 0)]));
  capas.forEach((capa) => {
    svg.append('g').selectAll('path').data(capa.filter((d) => d[1] > d[0])).join('path')
      .attr('fill', color.get(capa.key))
      .attr('d', (d) => {
        const esTope = tope.get(+d.data.fecha) === capa.key;
        const alturaSeg = y(d[0]) - y(d[1]) - (d[0] > 0 ? 1 : 0); // 1 px de aire entre segmentos
        return barraRedondeada(x(+d.data.fecha) + corrido, y(d[1]), ancho, Math.max(alturaSeg, 0), esTope ? 4 : 0, 'arriba');
      });
  });

  eventos.forEach((ev) => {
    const fila = tabla.find((d) => +d.fecha === +ev.fecha);
    if (!fila) return;
    const cx = x(+ev.fecha) + x.bandwidth() / 2, cy = y(d3.sum(series, (s) => fila[s]));
    svg.append('text').attr('class', 'grafico-nota').attr('x', cx).attr('y', cy - 8).attr('text-anchor', 'middle').text(ev.texto);
  });

  const tip = tooltip(el);
  svg.append('g').selectAll('rect').data(tabla).join('rect')
    .attr('x', (d) => x(+d.fecha)).attr('width', x.bandwidth()).attr('y', m.t).attr('height', alto - m.t - m.b)
    .attr('fill', 'transparent')
    .on('mousemove', (ev, d) => {
      const [px] = d3.pointer(ev, el);
      tip.mostrar(px, y(d3.sum(series, (s) => d[s])) * el.clientWidth / W, diaMes(d.fecha),
        [...series].reverse().map((s) => [color.get(s), s, formato.format(d[s])]));
    })
    .on('mouseleave', () => tip.ocultar());
}

// 2. Embudo: cada etapa centrada y proporcional, unida a la siguiente por un trapecio,
// con la tasa de paso y la mayor caída resaltada.
// etapas: [{etapa: 'Vio un producto', valor: 1000}, ...] en orden.
function embudo(el, etapas, { formato = entero, altoEtapa = 46, separacion = 26 } = {}) {
  el.textContent = '';
  const W = el.clientWidth;
  const izq = Math.min(170, W * 0.3), der = Math.min(120, W * 0.24);
  const centro = izq + (W - izq - der) / 2, maximo = W - izq - der;
  const alto = etapas.length * altoEtapa + (etapas.length - 1) * separacion;
  const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${W} ${alto}`).attr('width', '100%').attr('class', 'grafico');
  const w = d3.scaleLinear().domain([0, etapas[0].valor]).range([0, maximo]);
  const ancho = (v) => Math.max(w(v), 6);
  const pasos = etapas.slice(1).map((e, i) => e.valor / etapas[i].valor);
  const peor = pasos.indexOf(d3.min(pasos));
  const base = dash.colors[0];

  etapas.forEach((e, i) => {
    const top = i * (altoEtapa + separacion), a = ancho(e.valor);
    svg.append('rect').attr('x', centro - a / 2).attr('y', top).attr('width', a).attr('height', altoEtapa)
      .attr('rx', 4).attr('fill', base).attr('fill-opacity', 1 - i * (0.5 / Math.max(etapas.length - 1, 1)));
    svg.append('text').attr('class', 'grafico-etiqueta').attr('x', izq - 12).attr('y', top + altoEtapa / 2 - 7).attr('text-anchor', 'end').text(e.etapa);
    svg.append('text').attr('class', 'grafico-eje').attr('x', izq - 12).attr('y', top + altoEtapa / 2 + 10).attr('text-anchor', 'end')
      .text(i === 0 ? formato.format(e.valor) : `${formato.format(e.valor)} · ${pct1.format(e.valor / etapas[0].valor)}`);
    if (i === etapas.length - 1) return;
    const sig = etapas[i + 1], b = ancho(sig.valor), y0 = top + altoEtapa, y1 = y0 + separacion;
    svg.append('path').attr('fill', base).attr('fill-opacity', 0.14)
      .attr('d', `M${centro - a / 2},${y0}L${centro + a / 2},${y0}L${centro + b / 2},${y1}L${centro - b / 2},${y1}Z`);
    const esPeor = i === peor;
    svg.append('text').attr('class', esPeor ? 'grafico-paso grafico-paso-peor' : 'grafico-paso')
      .attr('x', W - der + 12).attr('y', y0 + separacion / 2).attr('dy', '0.32em')
      .text(`${pct1.format(pasos[i])} pasa`);
  });
}

// 3. Línea contra el período anterior, con eventos marcados.
// filas: [{fecha: Date, actual: 10, anterior: 8}], eventos: [{fecha: Date, texto}]
function lineaComparada(el, filas, { alto = 240, formato = compacto, eventos = [], nombres = ['Este período', 'Período anterior'] } = {}) {
  el.textContent = '';
  const W = el.clientWidth, m = { t: 22, r: 92, b: 22, l: 40 };
  const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${W} ${alto}`).attr('width', '100%').attr('class', 'grafico');
  const x = d3.scaleUtc().domain(d3.extent(filas, (d) => d.fecha)).range([m.l, W - m.r]);
  const y = d3.scaleLinear().domain([0, d3.max(filas, (d) => Math.max(d.actual, d.anterior ?? 0))]).nice().range([alto - m.b, m.t]);
  svg.append('g').selectAll('line').data(y.ticks(4)).join('line')
    .attr('x1', m.l).attr('x2', W - m.r).attr('y1', (d) => y(d)).attr('y2', (d) => y(d)).attr('stroke', 'var(--ds-chart-grid)');
  svg.append('g').attr('class', 'grafico-eje').selectAll('text').data(y.ticks(4)).join('text')
    .attr('x', m.l - 6).attr('y', (d) => y(d)).attr('dy', '0.32em').attr('text-anchor', 'end').text((d) => formato.format(d));
  svg.append('g').attr('class', 'grafico-eje').selectAll('text').data(x.ticks(Math.max(2, Math.floor((W - m.l - m.r) / 70)))).join('text')
    .attr('x', (d) => x(d)).attr('y', alto - 6).attr('text-anchor', 'middle').text((d) => diaMes(d));
  eventos.forEach((ev) => {
    svg.append('line').attr('x1', x(ev.fecha)).attr('x2', x(ev.fecha)).attr('y1', m.t).attr('y2', alto - m.b)
      .attr('stroke', 'var(--ds-chart-reference)').attr('stroke-dasharray', '3 3');
    svg.append('text').attr('class', 'grafico-nota').attr('x', x(ev.fecha) + 4).attr('y', m.t - 8).text(ev.texto);
  });
  const linea = (campo) => d3.line().defined((d) => d[campo] != null).x((d) => x(d.fecha)).y((d) => y(d[campo]));
  svg.append('path').datum(filas).attr('d', linea('anterior')).attr('fill', 'none')
    .attr('stroke', 'var(--ds-chart-muted)').attr('stroke-width', 2).attr('stroke-dasharray', '4 4');
  svg.append('path').datum(filas).attr('d', linea('actual')).attr('fill', 'none')
    .attr('stroke', dash.colors[0]).attr('stroke-width', 2).attr('stroke-linecap', 'round').attr('stroke-linejoin', 'round');
  const ult = filas[filas.length - 1];
  [[ult.actual, nombres[0], dash.colors[0]], [ult.anterior, nombres[1], 'var(--color-fg-muted)']].forEach(([v, n, c], i) => {
    if (v == null) return;
    svg.append('text').attr('class', 'grafico-final').attr('x', W - m.r + 8).attr('y', y(v) + (i ? 12 : -2))
      .attr('fill', c).text(`${n}: ${formato.format(v)}`);
  });
  const tip = tooltip(el), punto = svg.append('circle').attr('r', 4).attr('fill', dash.colors[0]).attr('opacity', 0);
  svg.append('rect').attr('x', m.l).attr('y', m.t).attr('width', W - m.l - m.r).attr('height', alto - m.t - m.b).attr('fill', 'transparent')
    .on('mousemove', (ev) => {
      const [px] = d3.pointer(ev);
      const d = filas[d3.bisectCenter(filas.map((f) => +f.fecha), +x.invert(px))];
      punto.attr('cx', x(d.fecha)).attr('cy', y(d.actual)).attr('opacity', 1);
      tip.mostrar(x(d.fecha) * el.clientWidth / W, y(d.actual), diaMes(d.fecha),
        [[dash.colors[0], nombres[0], formato.format(d.actual)], ['var(--ds-chart-muted)', nombres[1], d.anterior == null ? '—' : formato.format(d.anterior)]]);
    })
    .on('mouseleave', () => { tip.ocultar(); punto.attr('opacity', 0); });
}

// 4. Ranking horizontal: etiqueta a la izquierda, punta derecha redondeada, valor al final.
// filas: [{nombre: 'Calzado', valor: 123}], ya ordenadas.
function ranking(el, filas, { formato = compacto, altoFila = 30, destacar = 0 } = {}) {
  el.textContent = '';
  const W = el.clientWidth, izq = Math.min(150, W * 0.32), der = 64;
  const alto = filas.length * altoFila;
  const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${W} ${alto}`).attr('width', '100%').attr('class', 'grafico');
  const x = d3.scaleLinear().domain([0, d3.max(filas, (d) => d.valor)]).range([0, W - izq - der]);
  const alturaBarra = Math.min(altoFila - 10, 20);
  filas.forEach((d, i) => {
    const cy = i * altoFila + altoFila / 2;
    svg.append('text').attr('class', 'grafico-etiqueta').attr('x', izq - 10).attr('y', cy).attr('dy', '0.32em').attr('text-anchor', 'end').text(d.nombre);
    svg.append('path').attr('fill', i < destacar || destacar === 0 ? dash.colors[0] : 'var(--ds-chart-muted)')
      .attr('d', barraRedondeada(izq, cy - alturaBarra / 2, Math.max(x(d.valor), 2), alturaBarra, 4, 'derecha'));
    svg.append('text').attr('class', 'grafico-eje').attr('x', izq + Math.max(x(d.valor), 2) + 6).attr('y', cy).attr('dy', '0.32em').text(formato.format(d.valor));
  });
}
```

```css
/* Estilos de las recetas: van en el CSS de la página. */
.grafico { display: block; overflow: visible; font-family: var(--font-sans); }
.grafico-eje { fill: var(--color-fg-muted); font-size: var(--ds-font-size-caption); font-variant-numeric: tabular-nums; }
.grafico-etiqueta { fill: var(--color-fg); font-size: var(--ds-font-size-body); }
.grafico-nota { fill: var(--color-fg); font-size: var(--ds-font-size-caption); font-weight: var(--ds-font-weight-medium); }
.grafico-final { font-size: var(--ds-font-size-caption); font-weight: var(--ds-font-weight-medium); }
.grafico-paso { fill: var(--color-fg-muted); font-size: var(--ds-font-size-caption); font-variant-numeric: tabular-nums; }
.grafico-paso-peor { fill: var(--color-bad); font-weight: var(--ds-font-weight-medium); }
.grafico-leyenda { display: flex; flex-wrap: wrap; gap: var(--ds-gap-sm) var(--ds-gap-md); margin-bottom: var(--ds-gap-sm);
  font-size: var(--ds-font-size-caption); color: var(--color-fg-muted); }
.grafico-leyenda > span { display: inline-flex; align-items: center; gap: 6px; }
.grafico-tip { position: absolute; pointer-events: none; z-index: 2; min-width: 9rem;
  background: var(--ds-surface-popover); border: 1px solid var(--ds-border); border-radius: var(--ds-radius);
  box-shadow: var(--ds-shadow-popover); padding: var(--ds-pad-xs) var(--ds-pad-sm); font-size: var(--ds-font-size-caption);
  transition: opacity 120ms; }
.grafico-tip-titulo { color: var(--ds-text-secondary); margin-bottom: 2px; }
.grafico-tip-linea { display: flex; align-items: center; gap: 6px; }
.grafico-tip-linea b { margin-left: auto; padding-left: 12px; font-variant-numeric: tabular-nums; font-weight: var(--ds-font-weight-medium); }
.grafico-tip-punto { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
```
