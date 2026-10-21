import { useId, type CSSProperties, type ReactNode } from 'react';

/**
 * Lo que muestra el panel mientras el agente arma un dashboard que todavía no tiene
 * página: sobre una grilla aparecen los huecos punteados del layout, cada pieza llega
 * desde una dirección distinta y encaja en su lugar, crecen los gráficos y se prende
 * el punto "en vivo". En loop, sin textos ni números en el dibujo. Las animaciones
 * están en index.css (`armando-*`) y se apagan con prefers-reduced-motion.
 */

const KPIS = [16, 128, 240];
const CHART = { x: 16, y: 92, w: 216, h: 142 };
const SIDE = { x: 240, y: 92, w: 104, h: 142 };
const BARS = [44, 58, 52, 66, 61, 118, 63, 70, 74];
const RANK = [84, 75, 67];
const SERIES = ['var(--armando-a)', 'var(--armando-b)', 'var(--armando-c)'];

type Vars = CSSProperties & Record<`--${string}`, string | number>;

/** Una pieza que entra desde (dx, dy), girada, y se acomoda en su hueco. */
function Pieza({ dx, dy, rot, delay, children }: { dx: number; dy: number; rot: number; delay: number; children: ReactNode }) {
  const style: Vars = { '--dx': `${dx}px`, '--dy': `${dy}px`, '--rot': `${rot}deg`, animationDelay: `${delay}s` };
  return (
    <g className="armando-pieza" style={style}>
      <g className="armando-cuerpo" style={{ animationDelay: `${delay}s` }}>
        {children}
      </g>
    </g>
  );
}

/** Columna apilada con solo la punta redondeada. */
function Columna({ i }: { i: number }) {
  const base = CHART.y + CHART.h - 16;
  const step = (CHART.w - 28) / BARS.length;
  const x = CHART.x + 14 + i * step;
  const w = step * 0.62;
  const h = BARS[i] * 0.78;
  let y = base;
  const segs = [0.55, 0.28, 0.17].map((frac, j) => {
    const sh = h * frac;
    y -= sh;
    if (j === 2) {
      const r = 2.5;
      return <path key={j} fill={SERIES[j]} d={`M${x},${y + sh}v${-(sh - r)}a${r},${r} 0 0 1 ${r},${-r}h${w - 2 * r}a${r},${r} 0 0 1 ${r},${r}v${sh - r}z`} />;
    }
    return <rect key={j} x={x} y={y + 0.6} width={w} height={Math.max(sh - 0.6, 0)} fill={SERIES[j]} />;
  });
  return (
    <g className="armando-barra" style={{ animationDelay: `${0.06 * i}s` }}>
      {segs}
    </g>
  );
}

export function BuildingDashboard() {
  const grilla = `armando-grilla-${useId().replace(/:/g, '')}`;
  const base = CHART.y + CHART.h - 16;
  const dona = { cx: SIDE.x + SIDE.w / 2, cy: SIDE.y + 40, r: 22 };
  let inicio = 0;
  const huecos = [
    [16, 14, 110, 16, 4],
    [292, 14, 52, 16, 8],
    ...KPIS.map((x) => [x, 40, 104, 44, 7]),
    [CHART.x, CHART.y, CHART.w, CHART.h, 7],
    [SIDE.x, SIDE.y, SIDE.w, SIDE.h, 7],
  ];

  return (
    <div className="grid h-full place-items-center p-8" role="status" aria-live="polite">
      <div className="armando flex w-full max-w-md flex-col items-center gap-4">
        <svg viewBox="0 0 360 250" className="w-full overflow-visible" aria-hidden="true">
          <defs>
            <pattern id={grilla} width="12" height="12" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r=".9" className="fill-border" />
            </pattern>
          </defs>
          <rect x="1" y="1" width="358" height="248" rx="14" className="fill-card stroke-border" strokeWidth="1.5" />
          <rect x="8" y="8" width="344" height="234" rx="9" fill={`url(#${grilla})`} className="armando-grilla" />

          {/* Los huecos punteados donde va a encajar cada pieza. */}
          {huecos.map(([x, y, w, h, r], i) => (
            <rect key={i} x={x} y={y} width={w} height={h} rx={r} className="armando-hueco" />
          ))}

          {/* Título y filtro: desde arriba. */}
          <Pieza dx={-40} dy={-120} rot={-6} delay={0}>
            <rect x="16" y="17" width="96" height="9" rx="4.5" className="armando-titulo" />
          </Pieza>
          <Pieza dx={120} dy={-110} rot={10} delay={0.1}>
            <rect x="292" y="14" width="52" height="16" rx="8" className="armando-tarjeta" />
            <rect x="304" y="20" width="32" height="4" rx="2" className="armando-skel" />
          </Pieza>

          {/* KPIs: desde la izquierda, desde arriba y desde la derecha. */}
          {KPIS.map((x, i) => {
            const [dx, dy, rot] = ([[-240, 10, -8], [0, -170, 6], [240, -10, 9]] as const)[i];
            return (
              <Pieza key={x} dx={dx} dy={dy} rot={rot} delay={0.18 + 0.12 * i}>
                <rect x={x} y="40" width="104" height="44" rx="7" className="armando-tarjeta" />
                <rect x={x + 10} y="50" width="34" height="5" rx="2.5" className="armando-skel" />
                <rect x={x + 10} y="61" width={58 - i * 8} height="10" rx="5" fill="var(--armando-a)" className="armando-kpi" style={{ animationDelay: `${0.1 * i}s` }} />
                <rect x={x + 10} y="75" width="22" height="4" rx="2" fill="var(--armando-ok)" opacity=".8" />
              </Pieza>
            );
          })}

          {/* Gráfico grande: desde abajo a la izquierda. */}
          <Pieza dx={-200} dy={190} rot={-7} delay={0.52}>
            <rect x={CHART.x} y={CHART.y} width={CHART.w} height={CHART.h} rx="7" className="armando-tarjeta" />
            <rect x={CHART.x + 10} y={CHART.y + 10} width="70" height="6" rx="3" className="armando-titulo" />
            <line x1={CHART.x + 10} x2={CHART.x + CHART.w - 10} y1={base} y2={base} className="stroke-border" />
            {BARS.map((_, i) => (
              <Columna key={i} i={i} />
            ))}
          </Pieza>

          {/* Panel lateral: desde abajo a la derecha. Dona y ranking. */}
          <Pieza dx={200} dy={190} rot={8} delay={0.64}>
            <rect x={SIDE.x} y={SIDE.y} width={SIDE.w} height={SIDE.h} rx="7" className="armando-tarjeta" />
            <circle cx={dona.cx} cy={dona.cy} r={dona.r} fill="none" strokeWidth="9" className="armando-skel-trazo" />
            {[54, 33, 13].map((frac, j) => {
              const rot = -90 + inicio * 3.6;
              inicio += frac;
              const style: Vars = { '--l': frac - 1, animationDelay: `${[0, 0.25, 0.45][j]}s` };
              return (
                <circle
                  key={j}
                  cx={dona.cx}
                  cy={dona.cy}
                  r={dona.r}
                  pathLength={100}
                  stroke={SERIES[j]}
                  className="armando-arco"
                  transform={`rotate(${rot} ${dona.cx} ${dona.cy})`}
                  style={style}
                />
              );
            })}
            {RANK.map((w, i) => (
              <rect key={i} x={SIDE.x + 10} y={SIDE.y + 84 + i * 16} width={w} height="6" rx="3" fill="var(--armando-a)" className="armando-rank" style={{ animationDelay: `${0.1 * i}s` }} />
            ))}
          </Pieza>

          {/* En vivo: un punto que late en el filtro. */}
          <g className="armando-vivo">
            <circle cx="280" cy="22" r="3" fill="var(--armando-ok)" />
            <circle cx="280" cy="22" r="3" fill="var(--armando-ok)" className="armando-latido" />
          </g>
        </svg>
        <p className="text-sm font-medium text-muted-foreground">
          Armando el dashboard<span className="armando-puntos" />
        </p>
      </div>
    </div>
  );
}
