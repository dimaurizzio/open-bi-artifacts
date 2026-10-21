import {
  ArrowLeftIcon,
  CheckIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  CodeIcon,
  CopyIcon,
  DownloadIcon,
  ExternalLinkIcon,
  FileIcon,
  FileUpIcon,
  HistoryIcon,
  LinkIcon,
  LoaderIcon,
  MoreHorizontalIcon,
  PanelRightIcon,
  PencilIcon,
  PlayIcon,
  RefreshCwIcon,
  ScanSearchIcon,
  SigmaIcon,
  XIcon,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { MoveToProjectMenu } from '@/components/app/account';
import { TooltipIconButton } from '@/components/assistant-ui/tooltip-icon-button';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable';
import { Skeleton } from '@/components/ui/skeleton';
import { BuildingDashboard } from '@/components/app/building-dashboard';
import { Textarea } from '@/components/ui/textarea';
import { Toggle } from '@/components/ui/toggle';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { dashApi } from '@/lib/api';
import { ago, shortDate } from '@/lib/format';
import { highlightSql } from '@/lib/sql-highlight';
import type { DashDataset, DashDetail, DashRunsDoc, Inspection, MetaItem, Selection, SourceVersion, TestResult } from '@/lib/types';
import { MAX_ATTEMPTS, SETTLE_MS, problemsOf, repairMessage, settled } from '@/lib/repair';
import { useAppStore } from '@/lib/store';
import { cn } from '@/lib/utils';
import { setViewContext } from '@/lib/view-context';
import { DashFrame, type DashFrameHandle, type DashState } from './dash-frame';

interface Props {
  dashboardId: string;
  version: number;
  theme: 'light' | 'dark';
  solo: boolean;
  onClose: () => void;
}

type Drawer = { view: 'list' } | { view: 'source'; id: string; sel?: Selection } | { view: 'runs' } | { view: 'code' } | null;

const EMPTY_STATE: DashState = { sources: {}, calcs: [], loaders: [], params: {}, defaults: {} };
const FAILED = new Set(['error', 'missing', 'connect', 'declined']);

/**
 * Dashboard (contrato 2) con el modelo de Claude Dashboards: la página ocupa todo; la
 * barra ofrece refrescar, el modo Datos (cada número abre su fuente) y Fuentes, con la
 * consulta, la transformación y el resultado de cada valor.
 */
export function DashboardPanel({ dashboardId, version, theme, solo, onClose }: Props) {
  const [detail, setDetail] = useState<DashDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<DashState>(EMPTY_STATE);
  const [runs, setRuns] = useState<DashRunsDoc | null>(null);
  const [mode, setMode] = useState<'page' | 'data'>('page');
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [link, setLink] = useState('');
  const [pageSel, setPageSel] = useState<{ ids: string[]; at?: string }>({ ids: [] });
  const [copied, setCopied] = useState(false);
  const frame = useRef<DashFrameHandle>(null);

  const load = useCallback(async () => {
    try {
      const d = await dashApi.get(dashboardId);
      setDetail(d);
      setRuns((r) => r ?? d.runs);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [dashboardId]);

  useEffect(() => {
    setDetail(null);
    setState(EMPTY_STATE);
    setRuns(null);
    setMode('page');
    setDrawer(null);
    setLink('');
  }, [dashboardId]);

  useEffect(() => {
    void load();
  }, [load, version]);

  // La página se recarga solo si cambió lo que interpreta: archivos, fuentes o filtros.
  const frameKey = useMemo(() => {
    if (!detail) return '';
    const sig = JSON.stringify([detail.files, detail.params, detail.datasets]);
    let h = 0;
    for (let i = 0; i < sig.length; i++) h = (h * 31 + sig.charCodeAt(i)) | 0;
    return `${dashboardId}-${h}`;
  }, [dashboardId, detail]);

  const titleOf = useCallback(
    (id: string) => detail?.datasets[id]?.title ?? state.calcs.find((c) => c.id === id)?.title ?? id,
    [detail, state.calcs],
  );

  // Contexto de vista: viaja con cada mensaje mientras el dashboard está abierto. Sin valores.
  useEffect(() => {
    if (!detail) return;
    const failures = Object.entries(state.sources)
      .filter(([, s]) => FAILED.has(s.status))
      .map(([id]) => ({ id, label: titleOf(id) }));
    const loaded = Object.values(state.sources).some((s) => s.status !== 'loading');
    const open = drawer?.view === 'source' ? drawer : null;
    const selected = open?.sel?.ids ?? (pageSel.ids.length ? pageSel.ids : open ? [open.id] : undefined);
    setViewContext({
      dashboard: dashboardId,
      mode,
      place: link || undefined,
      selected: selected?.filter((id) => detail.datasets[id]),
      at: open?.sel?.at ?? pageSel.at,
      field: open?.sel?.field,
      filters: Object.keys(state.params).filter((k) => JSON.stringify(state.params[k]) !== JSON.stringify(state.defaults[k] ?? null)),
      failed: loaded ? failures.length : undefined,
      failures: failures.slice(0, 5),
      flagged: runs?.flagged || undefined,
      errors: runs?.errors?.length || undefined,
    });
  }, [dashboardId, detail, state, mode, link, drawer, pageSel, titleOf, runs]);
  useEffect(() => () => setViewContext(null), []);

  const onSelect = useCallback((sel: Selection) => setDrawer({ view: 'source', id: sel.id, sel }), []);

  // Reparación automática: las corridas que manda la página recién dibujada (no las guardadas).
  const [fresh, setFresh] = useState<DashRunsDoc | null>(null);
  const onFrameRuns = useCallback((doc: DashRunsDoc) => {
    setRuns(doc);
    setFresh(doc);
  }, []);
  useEffect(() => setFresh(null), [dashboardId]);
  const repair = useAppStore((s) => s.repair);
  const chatRunning = useAppStore((s) => (s.chatId ? (s.threads[s.chatId]?.running ?? false) : false));
  useEffect(() => {
    if (solo || !repair?.armed || repair.artifactId !== dashboardId || chatRunning || !settled(fresh)) return;
    const timer = window.setTimeout(() => {
      const st = useAppStore.getState();
      if (!st.repair?.armed || st.repair.artifactId !== dashboardId || st.chatId !== st.repair.chatId) return;
      const problems = problemsOf(fresh);
      if (problems.total === 0 || st.repair.attempts >= MAX_ATTEMPTS) st.clearRepair();
      else st.runRepair(repairMessage(problems, st.repair.attempts + 1));
    }, SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [solo, repair, fresh, dashboardId, chatRunning]);
  const changeMode = (m: 'page' | 'data') => {
    setMode(m);
    if (m === 'data' && !drawer) setDrawer({ view: 'list' });
  };
  const closeDrawer = () => {
    setDrawer(null);
    setMode('page');
  };

  const problems =
    Object.values(runs?.datasets ?? {}).filter((d) => d.status !== 'ok').length + (runs?.flagged ?? 0) + (runs?.errors?.length ?? 0);
  const updated = detail?.meta.updated_at;
  const refreshing = Object.values(state.sources).some((s) => s.refreshing || s.status === 'loading');

  return (
    <section className="flex h-full min-w-0 flex-col bg-background">
      <header className="flex h-12 shrink-0 items-center gap-1 border-b px-3">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {detail ? (
            <div className="min-w-0 leading-tight">
              <h2 className="truncate text-sm font-medium">{detail.meta.title}</h2>
              {updated && <p className="truncate text-[11px] text-muted-foreground">Editado {ago(updated)}</p>}
            </div>
          ) : (
            <Skeleton className="h-4 w-48" />
          )}
        </div>
        {problems > 0 && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 px-2 text-destructive hover:text-destructive"
            onClick={() => setDrawer({ view: 'runs' })}
          >
            <CircleAlertIcon className="size-3.5" />
            {problems} {problems === 1 ? 'problema' : 'problemas'}
          </Button>
        )}
        <TooltipIconButton tooltip="Refrescar las consultas en vivo" onClick={() => frame.current?.refresh()}>
          <RefreshCwIcon className={cn(refreshing && 'animate-spin [animation-duration:1.6s]')} />
        </TooltipIconButton>
        <Tooltip>
          <TooltipTrigger asChild>
            <Toggle
              size="sm"
              pressed={mode === 'data'}
              onPressedChange={(p) => changeMode(p ? 'data' : 'page')}
              aria-label="Modo datos"
              className="h-7 gap-1.5 px-2 text-xs"
            >
              <ScanSearchIcon className="size-3.5" /> Datos
            </Toggle>
          </TooltipTrigger>
          <TooltipContent>Clic en un número para ver de dónde sale</TooltipContent>
        </Tooltip>
        <TooltipIconButton
          tooltip={drawer ? 'Cerrar Fuentes' : 'Fuentes'}
          onClick={() => (drawer ? closeDrawer() : setDrawer({ view: 'list' }))}
          className={cn(drawer && 'bg-muted')}
        >
          <PanelRightIcon />
        </TooltipIconButton>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-7" aria-label="Más opciones">
              <MoreHorizontalIcon className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuItem onSelect={() => setDrawer({ view: 'code' })}>
              <CodeIcon /> Ver el código de la página
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => setDrawer({ view: 'runs' })}>
              <CircleAlertIcon /> Corridas de tu página
            </DropdownMenuItem>
            {detail && !solo && <MoveToProjectMenu artifactId={dashboardId} projectId={detail.project_id} onMoved={() => void load()} />}
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => window.open(`/a/${dashboardId}${link ? `#${link}` : ''}`, '_blank', 'noopener')}>
              <ExternalLinkIcon /> Abrir en una pestaña
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => {
                void navigator.clipboard.writeText(`${location.origin}/a/${dashboardId}${link ? `#${link}` : ''}`).then(() => {
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1500);
                });
              }}
            >
              {copied ? <CheckIcon /> : <LinkIcon />} {copied ? 'Enlace copiado' : 'Copiar enlace'}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        {!solo && (
          <TooltipIconButton tooltip="Cerrar" onClick={onClose}>
            <XIcon />
          </TooltipIconButton>
        )}
      </header>

      {error ? (
        <div className="m-4 flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          <CircleAlertIcon className="mt-0.5 size-4 shrink-0" />
          {error}
        </div>
      ) : (
        <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
          <ResizablePanel id="page" minSize={280}>
            {!detail ? (
              <div className="space-y-3 p-6">
                <Skeleton className="h-7 w-1/3" />
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-64 w-full" />
              </div>
            ) : detail.files['index.html'] ? (
              <DashFrame
                ref={frame}
                dashboardId={dashboardId}
                frameKey={frameKey}
                theme={theme}
                mode={mode}
                link={link}
                onState={setState}
                onRuns={onFrameRuns}
                onSelect={onSelect}
                onSelection={setPageSel}
                onLink={setLink}
              />
            ) : (
              chatRunning ? (
                <BuildingDashboard />
              ) : (
                <Empty text="Todavía no hay página: el agente la escribe después de las fuentes." />
              )
            )}
          </ResizablePanel>
          {drawer && detail && (
            <>
              <ResizableHandle />
              <ResizablePanel id="sources" defaultSize={360} minSize={280} maxSize="65">
                <aside className="flex h-full flex-col bg-sidebar/40">
                  {drawer.view === 'list' && (
                    <SourcesList
                      detail={detail}
                      state={state}
                      onOpen={(id) => setDrawer({ view: 'source', id })}
                      onHover={(id) => frame.current?.highlight(id)}
                      onClose={closeDrawer}
                      onChanged={load}
                    />
                  )}
                  {drawer.view === 'source' && (
                    <SourcePage
                      key={`${drawer.id}-${drawer.sel?.where ?? drawer.sel?.row ?? ''}-${drawer.sel?.field ?? ''}`}
                      dashboardId={dashboardId}
                      id={drawer.id}
                      sel={drawer.sel}
                      detail={detail}
                      state={state}
                      frame={frame}
                      onBack={() => setDrawer({ view: 'list' })}
                      onClose={closeDrawer}
                      onChanged={load}
                    />
                  )}
                  {drawer.view === 'runs' && (
                    <RunsView runs={runs} titleOf={titleOf} onClose={closeDrawer} onOpen={(id) => setDrawer({ view: 'source', id })} />
                  )}
                  {drawer.view === 'code' && <CodeView files={detail.files} onClose={closeDrawer} />}
                </aside>
              </ResizablePanel>
            </>
          )}
        </ResizablePanelGroup>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- piezas

function Empty({ text }: { text: string }) {
  return <div className="grid h-full place-items-center p-8 text-center text-sm text-muted-foreground">{text}</div>;
}

function DrawerHeader({ title, onBack, onClose, actions }: { title?: string; onBack?: () => void; onClose: () => void; actions?: ReactNode }) {
  return (
    <div className="flex h-11 shrink-0 items-center gap-1 px-2">
      {onBack && (
        <TooltipIconButton tooltip="Volver" onClick={onBack}>
          <ArrowLeftIcon />
        </TooltipIconButton>
      )}
      {title ? <h3 className="flex-1 truncate px-1 text-sm font-semibold">{title}</h3> : <span className="flex-1" />}
      {actions}
      <TooltipIconButton tooltip="Cerrar" onClick={onClose}>
        <XIcon />
      </TooltipIconButton>
    </div>
  );
}

function Section({ title, defaultOpen = false, children, aside }: { title: string; defaultOpen?: boolean; children: ReactNode; aside?: ReactNode }) {
  return (
    <Collapsible defaultOpen={defaultOpen} className="border-t">
      <div className="flex items-center pe-2">
        <CollapsibleTrigger className="group flex flex-1 items-center gap-1.5 px-3 py-2.5 text-sm font-semibold">
          <ChevronRightIcon className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
          {title}
        </CollapsibleTrigger>
        {aside}
      </div>
      <CollapsibleContent className="px-3 pb-3">{children}</CollapsibleContent>
    </Collapsible>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <TooltipIconButton
      tooltip={copied ? 'Copiado' : label}
      className="size-6"
      onClick={() =>
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        })
      }
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
    </TooltipIconButton>
  );
}

function CodeBlock({ children, copy }: { children: ReactNode; copy?: string }) {
  return (
    <div className="group relative rounded-lg border bg-muted/40">
      <pre className="max-h-80 overflow-auto p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">{children}</pre>
      {copy && (
        <div className="absolute end-1 top-1 opacity-0 transition-opacity group-hover:opacity-100">
          <CopyButton text={copy} label="Copiar" />
        </div>
      )}
    </div>
  );
}

const fmtValue = (v: unknown) =>
  v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : typeof v === 'number' ? v.toLocaleString('es-AR') : String(v);

/** Un SQL largo en una línea, una cláusula por línea (solo para mostrarlo). */
function layoutSql(sql: string) {
  if (sql.includes('\n')) return sql;
  return sql.replace(/\s+(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|QUALIFY|WINDOW|UNION ALL|LEFT JOIN|RIGHT JOIN|INNER JOIN|FULL JOIN|CROSS JOIN|JOIN)\b/gi, '\n$1');
}

function toCsv(rows: Record<string, unknown>[], columns: string[]) {
  const cell = (v: unknown) => {
    const s = v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map(cell).join(','), ...rows.map((r) => columns.map((c) => cell(r[c])).join(','))].join('\r\n');
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function sourceKind(ds?: DashDataset): 'query' | 'file' | 'paused' | 'rows' | 'unknown' {
  if (!ds) return 'unknown';
  if (ds.source.kind === 'query') return 'query';
  if (ds.source.kind === 'file') return ds.request ? 'paused' : 'file';
  if (ds.source.kind === 'rows') return 'rows';
  return 'unknown';
}

// ---------------------------------------------------------------- lista de Fuentes

function SourcesList({
  detail,
  state,
  onOpen,
  onHover,
  onClose,
  onChanged,
}: {
  detail: DashDetail;
  state: DashState;
  onOpen: (id: string) => void;
  onHover: (id: string | null) => void;
  onClose: () => void;
  onChanged: () => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const upload = async (file?: File) => {
    if (!file) return;
    try {
      setUploadError(null);
      await dashApi.upload(detail.meta.id, file);
      onChanged();
    } catch (e) {
      setUploadError((e as Error).message);
    }
  };

  const rows = [
    ...Object.entries(detail.datasets).map(([id, ds]) => {
      const st = state.sources[id];
      const kind = sourceKind(ds);
      const when = ago(st?.at ?? ds.updated?.at);
      const hover = kind === 'query' ? `${(ds.source as { connector?: string }).connector ?? ''}${when ? ` · ${when}` : ''}` : when;
      return { id, title: ds.title, hover, status: st?.status, refreshing: st?.refreshing, unused: detail.unused.includes(id), calc: false };
    }),
    ...state.calcs.map((c) => ({ id: c.id, title: c.title, hover: '', status: c.status as string | undefined, refreshing: false, unused: false, calc: true })),
  ];

  return (
    <>
      <DrawerHeader title="Fuentes" onClose={onClose} />
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-3">
        {rows.length === 0 && <Empty text="Todavía no hay fuentes." />}
        <ul className="flex flex-col">
          {rows.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => onOpen(r.id)}
                onMouseEnter={() => onHover(r.id)}
                onMouseLeave={() => onHover(null)}
                className="group flex w-full items-center gap-2 rounded-md px-2 py-2 text-start hover:bg-muted"
              >
                <span className="text-muted-foreground">
                  {r.calc ? <SigmaIcon className="size-4" /> : <FileIcon className="size-4" />}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{r.title}</p>
                  {r.hover && <p className="hidden truncate text-xs text-muted-foreground group-hover:block">{r.hover}</p>}
                </div>
                {r.unused && (
                  <Badge variant="outline" className="font-normal text-muted-foreground">
                    Sin usar
                  </Badge>
                )}
                {r.status === 'loading' || r.refreshing ? (
                  <LoaderIcon className="size-3.5 animate-spin text-muted-foreground" />
                ) : r.status && FAILED.has(r.status) ? (
                  <span className="size-1.5 rounded-full bg-destructive" aria-label="No cargó" />
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      </div>
      {detail.editor && (
        <div className="border-t p-2">
          <Button variant="ghost" size="sm" className="w-full justify-start text-muted-foreground" onClick={() => fileInput.current?.click()}>
            <FileUpIcon /> Adjuntar un archivo (CSV, TSV, JSON)
          </Button>
          <input ref={fileInput} type="file" accept=".csv,.tsv,.json" className="hidden" onChange={(e) => void upload(e.target.files?.[0])} />
          {uploadError && <p className="px-2 pt-1 text-xs text-destructive">{uploadError}</p>}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- página de una fuente

function SourcePage({
  dashboardId,
  id,
  sel,
  detail,
  state,
  frame,
  onBack,
  onClose,
  onChanged,
}: {
  dashboardId: string;
  id: string;
  sel?: Selection;
  detail: DashDetail;
  state: DashState;
  frame: React.RefObject<DashFrameHandle | null>;
  onBack: () => void;
  onClose: () => void;
  onChanged: () => void;
}) {
  const ds = detail.datasets[id];
  const calc = state.calcs.find((c) => c.id === id);
  const st = state.sources[id];
  const [info, setInfo] = useState<Inspection | null>(null);
  const kind = calc && !ds ? 'calc' : sourceKind(ds);

  // Lo que la página tiene de esta fuente: filas, meta, argumentos con filtros, loader o fórmula.
  useEffect(() => {
    let alive = true;
    void frame.current?.inspect(id).then((d) => alive && setInfo(d));
    return () => {
      alive = false;
    };
  }, [id, st?.at, st?.status, calc?.status, frame]);

  useEffect(() => {
    frame.current?.highlight(id);
    return () => frame.current?.highlight(null);
  }, [id, frame]);

  if (!ds && !calc) {
    return (
      <>
        <DrawerHeader onBack={onBack} onClose={onClose} />
        <Empty text="Esta fuente no existe en el dashboard." />
      </>
    );
  }

  const title = ds?.title ?? calc?.title ?? id;
  const description = ds?.description ?? calc?.description;
  const failed = st && FAILED.has(st.status) ? st : info && FAILED.has(info.status) ? { message: info.message, status: info.status } : null;
  const showCard = sel && sel.id === id && sel.shown !== undefined && (sel.field || kind === 'calc');

  return (
    <>
      <DrawerHeader
        onBack={onBack}
        onClose={onClose}
        actions={
          kind === 'query' ? (
            <TooltipIconButton tooltip="Refrescar esta consulta" onClick={() => frame.current?.refresh(id)}>
              <RefreshCwIcon className={cn((st?.refreshing || st?.status === 'loading') && 'animate-spin')} />
            </TooltipIconButton>
          ) : undefined
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="px-3 pb-3">
          <h3 className="text-base font-semibold">{title}</h3>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
          {failed && (
            <div className="mt-3 flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              <CircleAlertIcon className="mt-0.5 size-4 shrink-0" />
              <span className="min-w-0 break-words">{failed.message || 'No cargó.'}</span>
            </div>
          )}
          {kind === 'rows' && (
            <p className="mt-3 rounded-lg border px-3 py-2 text-sm text-muted-foreground">No cargó: es un formato viejo. Pedile al agente que lo pase a un archivo.</p>
          )}
          {showCard && (
            <div className="mt-3 rounded-xl border bg-background px-4 py-3">
              <p className="text-xs text-muted-foreground">{sel!.field ?? title}</p>
              <p className="mt-0.5 font-serif text-3xl tabular-nums">{sel!.shown || fmtValue(sel!.value)}</p>
              {sel!.where && <p className="mt-1 truncate text-xs text-muted-foreground">{decodeWhere(sel!.where)}</p>}
            </div>
          )}
        </div>

        {kind === 'query' && ds && (
          <QuerySection
            dashboardId={dashboardId}
            id={id}
            ds={ds}
            info={info}
            params={state.params}
            editor={detail.editor}
            onSaved={() => {
              onChanged();
            }}
          />
        )}
        {kind === 'paused' && ds && (
          <PausedSection dashboardId={dashboardId} id={id} ds={ds} editor={detail.editor} onChanged={onChanged} />
        )}
        {info?.loader && (
          <Section title="Transformación">
            {info.loader.description && <p className="mb-2 text-sm text-muted-foreground">{info.loader.description}</p>}
            <CodeBlock copy={info.loader.fn}>{info.loader.fn}</CodeBlock>
          </Section>
        )}
        {kind === 'calc' && calc && (
          <Section title="Fórmula">
            <div className="mb-2 flex flex-wrap gap-1">
              {calc.inputs.map((i) => (
                <Badge key={i} variant="secondary" className="font-normal">
                  {detail.datasets[i]?.title ?? i}
                </Badge>
              ))}
            </div>
            {info?.fn && <CodeBlock copy={info.fn}>{info.fn}</CodeBlock>}
          </Section>
        )}
        {kind === 'file' && ds && <FileSection dashboardId={dashboardId} ds={ds} stale={!!st?.stale} />}
        <ResultSection id={id} title={title} info={info} sel={sel} />
      </div>
    </>
  );
}

function decodeWhere(where: string) {
  return where
    .split('&')
    .map((p) => p.split('=').map((x) => decodeURIComponent(x)).join(' = '))
    .join(' · ');
}

function chip(values: Record<string, unknown>) {
  return (name: string, key: number) => {
    const v = values[name];
    const text = v === null || v === undefined ? 'NULL' : Array.isArray(v) ? `(${v.map((x) => JSON.stringify(x)).join(', ')})` : typeof v === 'string' ? `'${v}'` : String(v);
    return (
      <Tooltip key={key}>
        <TooltipTrigger asChild>
          <span className="mx-0.5 inline-flex items-center gap-1 rounded-md border border-primary/30 bg-primary/10 px-1 py-px align-baseline font-sans text-[11px] text-primary">
            <span className="font-medium">{name}</span>
            <span className="text-primary/80">{text}</span>
          </span>
        </TooltipTrigger>
        <TooltipContent>Filtro {name}: su valor ahora</TooltipContent>
      </Tooltip>
    );
  };
}

function QuerySection({
  dashboardId,
  id,
  ds,
  info,
  params,
  editor,
  onSaved,
}: {
  dashboardId: string;
  id: string;
  ds: DashDataset;
  info: Inspection | null;
  params: Record<string, unknown>;
  editor: boolean;
  onSaved: () => void;
}) {
  const source = ds.source as { connector: string; tool: string; args: Record<string, unknown> };
  const [editing, setEditing] = useState(false);
  const values = info?.values ?? params;
  return (
    <Section
      title="Consulta"
      aside={
        editor && !editing ? (
          <TooltipIconButton tooltip="Editar el pedido" className="size-7" onClick={() => setEditing(true)}>
            <PencilIcon />
          </TooltipIconButton>
        ) : undefined
      }
    >
      {editing ? (
        <SourceEditor dashboardId={dashboardId} id={id} source={source} values={values} onDone={() => setEditing(false)} onSaved={onSaved} />
      ) : (
        <>
          {Object.entries(source.args).map(([k, v]) => (
            <div key={k} className="mt-2 first:mt-0">
              <p className="pb-1 font-mono text-[11px] text-muted-foreground">{k}</p>
              {typeof v === 'string' ? (
                <CodeBlock copy={v}>{k === 'query' ? highlightSql(layoutSql(v), chip(values)) : v}</CodeBlock>
              ) : (
                <CodeBlock copy={JSON.stringify(v)}>{JSON.stringify(v, null, 1)}</CodeBlock>
              )}
            </div>
          ))}
          <p className="mt-2 text-xs text-muted-foreground">
            {source.connector} · <code className="font-mono">{source.tool}</code>
          </p>
        </>
      )}
    </Section>
  );
}

function SourceEditor({
  dashboardId,
  id,
  source,
  values,
  onDone,
  onSaved,
}: {
  dashboardId: string;
  id: string;
  source: { connector: string; tool: string; args: Record<string, unknown> };
  values: Record<string, unknown>;
  onDone: () => void;
  onSaved: () => void;
}) {
  const sqlKey = 'query' in source.args ? 'query' : Object.keys(source.args).find((k) => typeof source.args[k] === 'string') ?? 'query';
  const [sql, setSql] = useState(String(source.args[sqlKey] ?? ''));
  const others = Object.fromEntries(Object.entries(source.args).filter(([k]) => k !== sqlKey));
  const [rest, setRest] = useState(Object.keys(others).length ? JSON.stringify(others, null, 1) : '');
  const [busy, setBusy] = useState<'test' | 'save' | null>(null);
  const [result, setResult] = useState<TestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [versions, setVersions] = useState<SourceVersion[] | null>(null);

  const args = () => {
    const extra = rest.trim() ? (JSON.parse(rest) as Record<string, unknown>) : {};
    return { ...extra, [sqlKey]: sql };
  };
  const run = async (what: 'test' | 'save') => {
    setBusy(what);
    setErr(null);
    try {
      if (what === 'test') setResult(await dashApi.test(dashboardId, id, args(), values));
      else {
        await dashApi.save(dashboardId, id, args());
        onSaved();
        onDone();
      }
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <p className="font-mono text-[11px] text-muted-foreground">{sqlKey}</p>
      <Textarea value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false} className="min-h-40 font-mono text-xs" />
      {rest && (
        <>
          <p className="font-mono text-[11px] text-muted-foreground">otros argumentos (JSON)</p>
          <Textarea value={rest} onChange={(e) => setRest(e.target.value)} spellCheck={false} className="min-h-16 font-mono text-xs" />
        </>
      )}
      {err && <p className="text-xs break-words text-destructive">{err}</p>}
      {result && (
        <div className="rounded-lg border p-2">
          <p className="pb-1 text-xs text-muted-foreground">
            Prueba: {result.rows.toLocaleString('es-AR')} filas{result.cut ? ' (cortadas)' : ''} · {(result.ms / 1000).toFixed(1)} s
          </p>
          <RowsTable rows={result.data.slice(0, 50)} columns={result.columns} />
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" variant="outline" disabled={!!busy} onClick={() => void run('test')}>
          {busy === 'test' ? <LoaderIcon className="animate-spin" /> : <PlayIcon />} Probar
        </Button>
        <Button size="sm" disabled={!!busy} onClick={() => void run('save')}>
          {busy === 'save' ? <LoaderIcon className="animate-spin" /> : <CheckIcon />} Guardar
        </Button>
        <Button size="sm" variant="ghost" disabled={!!busy} onClick={onDone}>
          Cancelar
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="ms-auto text-muted-foreground"
          onClick={() => void dashApi.versions(dashboardId, id).then(setVersions).catch((e) => setErr((e as Error).message))}
        >
          <HistoryIcon /> Anteriores
        </Button>
      </div>
      {versions && (
        <ul className="flex flex-col gap-1 rounded-lg border p-1.5">
          {versions.length === 0 && <li className="px-1.5 py-1 text-xs text-muted-foreground">No hay versiones anteriores.</li>}
          {versions.map((v) => {
            const q = (v.data.source as { args?: Record<string, unknown> }).args?.[sqlKey];
            return (
              <li key={v.version} className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-muted">
                <span className="shrink-0 text-xs text-muted-foreground">{shortDate(v.at)}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-[11px]">{typeof q === 'string' ? q : v.data.source.kind}</span>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-2 text-xs"
                  onClick={() =>
                    void dashApi
                      .restore(dashboardId, id, v.version)
                      .then(() => {
                        onSaved();
                        onDone();
                      })
                      .catch((e) => setErr((e as Error).message))
                  }
                >
                  Restaurar
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function PausedSection({ dashboardId, id, ds, editor, onChanged }: { dashboardId: string; id: string; ds: DashDataset; editor: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const req = ds.request!;
  const query = typeof req.args.query === 'string' ? req.args.query : JSON.stringify(req.args);
  return (
    <Section title="Consulta" defaultOpen>
      <p className="mb-2 text-sm text-muted-foreground">
        Esta consulta no se actualiza sola: todos ven los resultados guardados {ds.updated?.at ? ago(ds.updated.at) : ''}
        {ds.updated?.by ? ` por ${ds.updated.by}` : ''}, sin importar su acceso.
      </p>
      <CodeBlock copy={query}>{highlightSql(layoutSql(query))}</CodeBlock>
      <p className="mt-2 text-xs text-muted-foreground">
        {req.connector} · <code className="font-mono">{req.tool}</code>
      </p>
      {ds.lastAttempt && !ds.lastAttempt.ok && ds.lastAttempt.error && (
        <p className="mt-2 text-xs text-destructive">Último intento: {ds.lastAttempt.error}</p>
      )}
      {editor && (
        <div className="mt-2">
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void dashApi
                .resume(dashboardId, id)
                .then(onChanged)
                .catch((e) => setErr((e as Error).message))
                .finally(() => setBusy(false));
            }}
          >
            {busy ? <LoaderIcon className="animate-spin" /> : <PlayIcon />} Volver a consultar en vivo
          </Button>
          <p className="mt-1 text-xs text-muted-foreground">Cada lector la corre con su propio acceso. La página no puede deshacerlo.</p>
          {err && <p className="mt-1 text-xs text-destructive">{err}</p>}
        </div>
      )}
    </Section>
  );
}

function FileSection({ dashboardId, ds, stale }: { dashboardId: string; ds: DashDataset; stale: boolean }) {
  const src = ds.source as { url: string; name: string };
  return (
    <Section title="Archivo" defaultOpen>
      <div className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm">
        <FileIcon className="size-4 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate">{src.name}</span>
        <a href={dashApi.assetUrl(dashboardId, src.url)} className="text-muted-foreground hover:text-foreground" aria-label="Descargar el archivo">
          <DownloadIcon className="size-4" />
        </a>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {ds.updated?.at ? `Actualizado ${ago(ds.updated.at)}${ds.updated.by ? ` por ${ds.updated.by}` : ''}.` : 'Sin fecha de actualización.'}
        {ds.schedule ? ` ${ds.schedule}.` : ''}
        {stale ? ' Está desactualizado.' : ''} Cualquiera que abra el dashboard puede leerlo.
      </p>
    </Section>
  );
}

function ResultSection({ id, title, info, sel }: { id: string; title: string; info: Inspection | null; sel?: Selection }) {
  const rows = Array.isArray(info?.data) ? (info!.data as Record<string, unknown>[]) : null;
  const columns = info?.columns ?? (rows && rows[0] && typeof rows[0] === 'object' ? Object.keys(rows[0]) : []);
  const tabular = rows && rows.every((r) => r && typeof r === 'object' && !Array.isArray(r));
  const pick = sel && sel.id === id ? sel : undefined;
  return (
    <Section
      title="Resultado"
      defaultOpen={!!pick}
      aside={
        tabular && rows!.length > 0 ? (
          <TooltipIconButton
            tooltip="Descargar CSV"
            className="size-7"
            onClick={() => download(`${title.replace(/[^\w\s-]+/g, '').trim() || id}.csv`, toCsv(rows!, columns))}
          >
            <DownloadIcon />
          </TooltipIconButton>
        ) : undefined
      }
    >
      {!info || info.status === 'loading' || info.status === 'idle' ? (
        <Skeleton className="h-24 w-full" />
      ) : tabular ? (
        <>
          <p className="pb-1.5 text-xs text-muted-foreground">
            {(info.rows ?? rows!.length).toLocaleString('es-AR')} filas{info.cut ? ', cortadas' : ''}
            {info.at ? ` · ${ago(info.at)}` : ''}
            {info.stale ? ' · en caché' : ''}
          </p>
          <RowsTable rows={rows!} columns={columns} pickRow={pick?.rowIndex} pickField={pick?.field} />
        </>
      ) : info.data !== undefined ? (
        <CodeBlock copy={JSON.stringify(info.data, null, 1)}>{JSON.stringify(info.data, null, 1)}</CodeBlock>
      ) : (
        <p className="text-sm text-muted-foreground">Sin resultado.</p>
      )}
      {info?.meta && info.meta.length > 0 && <MetaList meta={info.meta} />}
    </Section>
  );
}

function RowsTable({ rows, columns, pickRow, pickField }: { rows: Record<string, unknown>[]; columns: string[]; pickRow?: number; pickField?: string }) {
  const limit = 500;
  const start = pickRow !== undefined && pickRow >= limit ? Math.max(0, pickRow - 20) : 0;
  const shown = rows.slice(start, start + limit);
  const pickRef = useRef<HTMLTableCellElement>(null);
  useEffect(() => {
    pickRef.current?.scrollIntoView({ block: 'center', inline: 'center' });
  }, [pickRow, pickField]);
  return (
    <div className="max-h-96 overflow-auto rounded-lg border">
      <table className="w-full border-collapse text-xs">
        <thead className="sticky top-0 bg-muted">
          <tr>
            {columns.map((c) => (
              <th key={c} className="border-b px-2 py-1.5 text-start font-medium whitespace-nowrap">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((r, i) => {
            const index = start + i;
            return (
              <tr key={index} className={cn(index === pickRow && 'bg-primary/5')}>
                {columns.map((c) => {
                  const lit = index === pickRow && c === pickField;
                  const v = r[c];
                  return (
                    <td
                      key={c}
                      ref={lit ? pickRef : undefined}
                      className={cn(
                        'border-b px-2 py-1 whitespace-nowrap tabular-nums',
                        typeof v === 'number' && 'text-end',
                        lit && 'bg-primary/15 font-semibold ring-1 ring-primary/50 ring-inset',
                      )}
                    >
                      {fmtValue(v)}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      {rows.length > shown.length && (
        <p className="px-2 py-1.5 text-xs text-muted-foreground">
          Se muestran {shown.length.toLocaleString('es-AR')} de {rows.length.toLocaleString('es-AR')}: el CSV las trae todas.
        </p>
      )}
    </div>
  );
}

function MetaList({ meta }: { meta: MetaItem[] }) {
  return (
    <dl className="mt-3 flex flex-col gap-2">
      {meta.map((m, i) => (
        <div key={i}>
          <dt className="pb-0.5 text-xs text-muted-foreground">{m.label}</dt>
          <dd className="text-sm">
            {m.type === 'code' ? (
              <CodeBlock copy={m.value}>{m.lang === 'sql' ? highlightSql(layoutSql(m.value)) : m.value}</CodeBlock>
            ) : (
              m.value
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// ---------------------------------------------------------------- corridas y código

function RunsView({
  runs,
  titleOf,
  onClose,
  onOpen,
}: {
  runs: DashRunsDoc | null;
  titleOf: (id: string) => string;
  onClose: () => void;
  onOpen: (id: string) => void;
}) {
  const failed = Object.entries(runs?.datasets ?? {}).filter(([, d]) => d.status !== 'ok');
  const fine = Object.entries(runs?.datasets ?? {}).filter(([, d]) => d.status === 'ok');
  return (
    <>
      <DrawerHeader title="Corridas de tu página" onClose={onClose} />
      <p className="px-3 pb-2 text-xs text-muted-foreground">
        Lo que tu página reportó al abrirse: fuentes que no cargaron, números que no coinciden con sus datos y errores del código. El agente lo lee para
        corregir.
      </p>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 pb-3 text-sm">
        {!runs && <Empty text="Todavía no hay corridas." />}
        {failed.map(([id, d]) => (
          <button key={id} type="button" onClick={() => onOpen(id)} className="block w-full rounded-lg border p-2.5 text-start hover:bg-muted">
            <p className="flex items-center gap-1.5 font-medium">
              <CircleAlertIcon className="size-3.5 text-destructive" /> {titleOf(id)}
              <Badge variant="outline" className="ms-auto font-normal">
                {d.status}
              </Badge>
            </p>
            {d.message && <p className="mt-1 text-xs break-words text-muted-foreground">{d.message}</p>}
          </button>
        ))}
        {(runs?.marks ?? []).length > 0 && (
          <div className="rounded-lg border p-2.5">
            <p className="font-medium">
              {runs!.flagged} {runs!.flagged === 1 ? 'número no coincide' : 'números no coinciden'} con sus datos
            </p>
            <ul className="mt-1.5 space-y-1 text-xs text-muted-foreground">
              {runs!.marks!.map((m, i) => (
                <li key={i}>
                  <code>{m.mark}</code> · {titleOf(m.source)}
                  {m.field ? `.${m.field}` : ''}: {m.reason ? m.reason : `muestra “${m.shown}”, el dato es ${fmtValue(m.inData)}`}
                </li>
              ))}
            </ul>
          </div>
        )}
        {(runs?.errors ?? []).map((e, i) => (
          <div key={i} className="rounded-lg border p-2.5">
            <p className="font-medium">
              {e.kind === 'stopped' ? 'El código se detuvo' : e.kind === 'rejection' ? 'Una promesa falló' : 'Error del código'}
              {e.count > 1 ? ` ×${e.count}` : ''}
            </p>
            <p className="mt-1 text-xs break-words text-muted-foreground">
              {e.name}: {e.message}
              {e.file ? ` (${e.file})` : ''}
            </p>
          </div>
        ))}
        {runs && fine.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Cargaron bien: {fine.map(([id]) => titleOf(id)).join(', ')}.
          </p>
        )}
      </div>
    </>
  );
}

function CodeView({ files, onClose }: { files: Record<string, string>; onClose: () => void }) {
  const names = Object.keys(files).sort((a, b) => (a === 'index.html' ? -1 : b === 'index.html' ? 1 : a.localeCompare(b)));
  const [name, setName] = useState(names[0] ?? '');
  return (
    <>
      <DrawerHeader title="Código de la página" onClose={onClose} actions={files[name] ? <CopyButton text={files[name]} label="Copiar el archivo" /> : undefined} />
      {names.length > 1 && (
        <div className="flex flex-wrap gap-1 px-3 pb-2">
          {names.map((n) => (
            <Button key={n} size="sm" variant={n === name ? 'secondary' : 'ghost'} className="h-6 px-2 font-mono text-[11px]" onClick={() => setName(n)}>
              {n}
            </Button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto px-3 pb-3">
        {files[name] ? <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap">{files[name]}</pre> : <Empty text="Sin página todavía." />}
      </div>
    </>
  );
}
