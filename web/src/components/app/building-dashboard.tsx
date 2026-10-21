/**
 * Lo que muestra el panel mientras el agente arma un dashboard que todavía no tiene
 * página: una tarjeta que se construye sola y un cursor que trabaja sobre los
 * gráficos, en loop. Las animaciones están en index.css (`armando-*`) y se apagan con
 * prefers-reduced-motion.
 */
export function BuildingDashboard() {
  const bars = [62, 88, 54, 104, 76, 118];
  return (
    <div className="grid h-full place-items-center p-8" role="status" aria-live="polite">
      <div className="flex w-full max-w-sm flex-col items-center gap-5 text-center">
        <svg viewBox="0 0 320 220" className="armando w-full text-blue-600 dark:text-blue-500" aria-hidden="true">
          {/* Tarjeta y título */}
          <rect x="1" y="1" width="318" height="218" rx="12" className="fill-card stroke-border" strokeWidth="1.5" />
          <rect x="20" y="20" width="110" height="9" rx="4.5" className="armando-shimmer fill-muted" />
          <rect x="20" y="35" width="70" height="6" rx="3" className="armando-shimmer fill-muted" style={{ animationDelay: '0.2s' }} />

          {/* KPIs */}
          {[0, 1, 2].map((i) => (
            <g key={i} transform={`translate(${20 + i * 96} 54)`}>
              <rect width="88" height="40" rx="6" className="fill-muted/60" />
              <rect x="10" y="10" width="34" height="5" rx="2.5" className="fill-muted-foreground/40" />
              <rect x="10" y="22" width="52" height="9" rx="4.5" className="armando-kpi fill-current" style={{ animationDelay: `${0.25 * i}s` }} />
            </g>
          ))}

          {/* Barras: crecen desde la base, con solo la punta redondeada */}
          <line x1="20" x2="160" y1="198" y2="198" className="stroke-border" strokeWidth="1" />
          {bars.map((h, i) => (
            <rect
              key={i}
              x={26 + i * 22}
              y={198 - h * 0.78}
              width="14"
              height={h * 0.78}
              rx="3"
              className="armando-bar fill-current"
              style={{ animationDelay: `${0.12 * i}s`, opacity: 0.55 + i * 0.08 }}
            />
          ))}

          {/* Línea: se dibuja de izquierda a derecha */}
          <line x1="176" x2="300" y1="198" y2="198" className="stroke-border" strokeWidth="1" />
          <polyline
            points="178,180 198,168 218,174 238,146 258,152 278,124 298,118"
            pathLength="1"
            className="armando-line stroke-current"
            fill="none"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Cursor que trabaja: va a las barras, a la línea y a un KPI, y hace clic */}
          <g className="armando-cursor">
            <circle r="9" className="armando-click fill-current" />
            <path
              d="M0 0 L0 15 L4 11.5 L7 18 L9.5 17 L6.5 10.5 L11.5 10.5 Z"
              className="fill-foreground stroke-background"
              strokeWidth="1.2"
              strokeLinejoin="round"
            />
          </g>
        </svg>
        <div className="space-y-1">
          <p className="text-sm font-medium">
            Armando el dashboard<span className="armando-dots" />
          </p>
          <p className="text-xs text-muted-foreground">
            El agente está escribiendo las consultas y los gráficos. La página aparece acá apenas esté lista.
          </p>
        </div>
      </div>
    </div>
  );
}
