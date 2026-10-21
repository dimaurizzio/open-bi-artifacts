import {
  ArrowLeftIcon,
  CheckIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  CodeIcon,
  CopyIcon,
  DatabaseIcon,
  ExternalLinkIcon,
  FileUpIcon,
  InfoIcon,
  LinkIcon,
  LoaderIcon,
  MoreHorizontalIcon,
  MousePointer2Icon,
  RefreshCwIcon,
  SigmaIcon,
  XIcon,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
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
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { api } from '@/lib/api';
import { shortDate } from '@/lib/format';
import { highlightSql } from '@/lib/sql-highlight';
import type { ArtifactDetail, CalcInfo, Dataset, RunRecord, RunsSummary } from '@/lib/types';
import { useAppStore } from '@/lib/store';
import { cn } from '@/lib/utils';
import { DashboardPanel } from './dashboard-panel';
import { ViewerFrame, type SourceStatus, type ViewerHandle } from './viewer-frame';

interface Props {
  artifactId: string;
  version: number; // sube cuando el agente escribió algo en este artifact
  theme: 'light' | 'dark';
  solo: boolean;
  onClose: () => void;
}

type Drawer = { view: 'list' } | { view: 'source'; id: string } | { view: 'issues' } | { view: 'code' } | null;

const CONNECTOR: Record<string, string> = { bigquery: 'Google Cloud BigQuery', file: 'Archivo adjunto' };
const MODE_LABEL: Record<Dataset['mode'], string> = { live: 'En vivo', saved: 'Guardado', file: 'Archivo' };
const issuesOf = (r?: RunRecord) => (r ? r.source_failures.length + r.mismatches.length + r.errors.length : 0);

/**
 * Panel del artifact abierto. Los dashboards del contrato 2 (el modelo de Claude
 * Dashboards) usan DashboardPanel; los artifacts viejos, su visor de siempre.
 */
export function ArtifactPanel(props: Props) {
  const known = useAppStore((s) => s.artifacts.find((a) => a.id === props.artifactId));
  const [contract, setContract] = useState<number | null>(known ? (known.contract ?? 1) : null);
  useEffect(() => {
    if (known) {
      setContract(known.contract ?? 1);
      return;
    }
    let alive = true;
    setContract(null);
    api
      .getArtifact(props.artifactId)
      .then((d) => alive && setContract('contract' in d ? 2 : 1))
      .catch(() => alive && setContract(1));
    return () => {
      alive = false;
    };
  }, [props.artifactId, known]);
  if (contract === null) {
    return (
      <div className="space-y-3 p-6">
        <Skeleton className="h-7 w-1/3" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (contract === 2) {
    return <DashboardPanel dashboardId={props.artifactId} version={props.version} theme={props.theme} solo={props.solo} onClose={props.onClose} />;
  }
  return <LegacyArtifactPanel {...props} />;
}

/**
 * Visor de artifacts del contrato 1: la página ocupa todo, y una barra fina ofrece
 * refrescar y cambiar entre "usar la página" y "ver de dónde salen los números".
 */
function LegacyArtifactPanel({ artifactId, version, theme, solo, onClose }: Props) {
  const [detail, setDetail] = useState<ArtifactDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [calcs, setCalcs] = useState<CalcInfo[]>([]);
  const [runs, setRuns] = useState<RunsSummary | null>(null);
  const [statuses, setStatuses] = useState<Record<string, SourceStatus>>({});
  const [mode, setMode] = useState<'page' | 'sources'>('page');
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [copiedLink, setCopiedLink] = useState(false);
  const viewer = useRef<ViewerHandle>(null);
  const runTimer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    try {
      const d = await api.getArtifact(artifactId);
      setDetail(d);
      setRuns(d.runs);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [artifactId]);

  useEffect(() => {
    setDetail(null);
    setCalcs([]);
    setStatuses({});
    setMode('page');
    setDrawer(null);
  }, [artifactId]);

  useEffect(() => {
    load();
  }, [load, version]);

  // El visor se recarga solo si cambió lo que interpreta: página, fuentes o filtros.
  const frameKey = useMemo(() => {
    if (!detail) return '';
    const sig = JSON.stringify([detail.page, detail.params, detail.datasets]);
    let h = 0;
    for (let i = 0; i < sig.length; i++) h = (h * 31 + sig.charCodeAt(i)) | 0;
    return `${artifactId}-${h}`;
  }, [artifactId, detail]);

  const onRun = useCallback(
    (record: RunRecord) => {
      window.clearTimeout(runTimer.current);
      runTimer.current = window.setTimeout(async () => {
        await api.postRun(artifactId, record).catch(() => undefined);
        const d = await api.getArtifact(artifactId).catch(() => null);
        if (d) setRuns(d.runs);
      }, 1500);
    },
    [artifactId],
  );

  const changeMode = (m: 'page' | 'sources') => {
    setMode(m);
    setDrawer(m === 'sources' ? { view: 'list' } : null);
  };
  const openSource = useCallback((id: string) => {
    setMode('sources');
    setDrawer({ view: 'source', id });
  }, []);
  const closeDrawer = () => {
    setDrawer(null);
    setMode('page');
  };

  const lastRun = runs?.recent[runs.recent.length - 1];
  const issues = issuesOf(lastRun);
  const updated = detail?.meta.updated_at;

  return (
    <section className="flex h-full min-w-0 flex-col bg-background">
      {/* Barra fina del visor */}
      <header className="flex h-12 shrink-0 items-center gap-1.5 border-b px-3">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {detail ? (
            <>
              <Badge variant="outline" className="shrink-0 font-normal text-muted-foreground">
                {detail.meta.kind === 'analysis' ? 'Análisis' : 'Dashboard'}
              </Badge>
              <div className="min-w-0 leading-tight">
                <h2 className="truncate text-sm font-medium" title={detail.meta.description}>
                  {detail.meta.title}
                </h2>
                {updated && <p className="truncate text-[11px] text-muted-foreground">Editado {shortDate(updated)}</p>}
              </div>
            </>
          ) : (
            <Skeleton className="h-4 w-48" />
          )}
        </div>

        {issues > 0 && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 px-2 text-destructive hover:text-destructive"
            onClick={() => setDrawer({ view: 'issues' })}
          >
            <CircleAlertIcon className="size-3.5" />
            {issues} {issues === 1 ? 'problema' : 'problemas'}
          </Button>
        )}
        <TooltipIconButton tooltip="Volver a correr todas las consultas en vivo" onClick={() => viewer.current?.reload()}>
          <RefreshCwIcon />
        </TooltipIconButton>
        <ToggleGroup
          type="single"
          size="sm"
          variant="outline"
          value={mode}
          onValueChange={(v) => v && changeMode(v as 'page' | 'sources')}
          className="h-7"
        >
          <Tooltip>
            <TooltipTrigger asChild>
              <ToggleGroupItem value="page" aria-label="Usar la página" className="h-7 w-8 px-0">
                <MousePointer2Icon className="size-3.5" />
              </ToggleGroupItem>
            </TooltipTrigger>
            <TooltipContent>Usar la página</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <ToggleGroupItem value="sources" aria-label="Ver de dónde salen los números" className="h-7 w-8 px-0">
                <DatabaseIcon className="size-3.5" />
              </ToggleGroupItem>
            </TooltipTrigger>
            <TooltipContent>Ver de dónde salen los números</TooltipContent>
          </Tooltip>
        </ToggleGroup>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-7" aria-label="Más opciones">
              <MoreHorizontalIcon className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuItem onSelect={() => setDrawer({ view: 'code' })}>
              <CodeIcon /> Ver código de la página
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => setDrawer({ view: 'issues' })}>
              <CircleAlertIcon /> Corridas y problemas
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => window.open(`/a/${artifactId}`, '_blank', 'noopener')}>
              <ExternalLinkIcon /> Abrir en una pestaña
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => {
                navigator.clipboard.writeText(`${location.origin}/a/${artifactId}`).then(() => {
                  setCopiedLink(true);
                  window.setTimeout(() => setCopiedLink(false), 1500);
                });
              }}
            >
              {copiedLink ? <CheckIcon /> : <LinkIcon />} {copiedLink ? 'Enlace copiado' : 'Copiar enlace'}
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
            ) : detail.page ? (
              <ViewerFrame
                ref={viewer}
                artifactId={artifactId}
                frameKey={frameKey}
                theme={theme}
                mode={mode}
                onRun={onRun}
                onCalcs={setCalcs}
                onStatus={setStatuses}
                onSelect={openSource}
              />
            ) : (
              <Empty text="El agente todavía no escribió la página." />
            )}
          </ResizablePanel>
          {drawer && detail && (
            <>
              <ResizableHandle />
              <ResizablePanel id="drawer" defaultSize={340} minSize={260} maxSize="65">
                <aside className="flex h-full flex-col bg-sidebar/40">
                  {drawer.view === 'list' && (
                    <SourcesList
                      detail={detail}
                      calcs={calcs}
                      statuses={statuses}
                      solo={solo}
                      onOpen={(id) => setDrawer({ view: 'source', id })}
                      onHighlight={(id) => viewer.current?.highlight(id)}
                      onClose={closeDrawer}
                      onChanged={load}
                    />
                  )}
                  {drawer.view === 'source' && (
                    <SourceDetail
                      id={drawer.id}
                      dataset={detail.datasets[drawer.id]}
                      calc={calcs.find((c) => c.id === drawer.id)}
                      status={statuses[drawer.id]}
                      failure={lastRun?.source_failures.find((f) => f.dataset === drawer.id)?.error}
                      onBack={() => setDrawer({ view: 'list' })}
                      onRefresh={() => viewer.current?.reload(drawer.id)}
                      onClose={closeDrawer}
                    />
                  )}
                  {drawer.view === 'issues' && <IssuesView runs={runs} onClose={closeDrawer} onOpenSource={openSource} />}
                  {drawer.view === 'code' && <CodeView page={detail.page} onClose={closeDrawer} />}
                </aside>
              </ResizablePanel>
            </>
          )}
        </ResizablePanelGroup>
      )}
    </section>
  );
}

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
      {title && <h3 className="flex-1 truncate px-1 text-sm font-semibold">{title}</h3>}
      {!title && <span className="flex-1" />}
      {actions}
      <TooltipIconButton tooltip="Cerrar" onClick={onClose}>
        <XIcon />
      </TooltipIconButton>
    </div>
  );
}

function statusLine(ds: Dataset, st?: SourceStatus, failure?: string): { text: string; tone: 'muted' | 'error' | 'loading' } {
  if (st?.loading) return { text: 'Consultando…', tone: 'loading' };
  const err = st?.error ?? failure;
  if (err) return { text: err, tone: 'error' };
  if (st?.cached_at) {
    const rows = st.rows != null ? `${st.rows.toLocaleString('es-AR')} filas · ` : '';
    const when = ds.mode === 'live' ? (st.stale ? 'en caché, actualizando' : `actualizado ${shortDate(st.cached_at)}`) : `guardado ${shortDate(st.cached_at)}`;
    return { text: `${rows}${when}`, tone: 'muted' };
  }
  return { text: MODE_LABEL[ds.mode], tone: 'muted' };
}

function SourcesList({
  detail,
  calcs,
  statuses,
  solo,
  onOpen,
  onHighlight,
  onClose,
  onChanged,
}: {
  detail: ArtifactDetail;
  calcs: CalcInfo[];
  statuses: Record<string, SourceStatus>;
  solo: boolean;
  onOpen: (id: string) => void;
  onHighlight: (id: string | null) => void;
  onClose: () => void;
  onChanged: () => void;
}) {
  const datasets = Object.values(detail.datasets);
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const upload = async (file?: File) => {
    if (!file) return;
    try {
      setUploadError(null);
      await api.upload(detail.meta.id, file);
      onChanged();
    } catch (e) {
      setUploadError((e as Error).message);
    }
  };

  const Item = ({ id, title, sub, tone, icon }: { id: string; title: string; sub: string; tone: string; icon: ReactNode }) => (
    <li>
      <button
        type="button"
        onClick={() => onOpen(id)}
        onMouseEnter={() => onHighlight(id)}
        onMouseLeave={() => onHighlight(null)}
        className="group flex w-full items-center gap-2 rounded-md px-2 py-2 text-start hover:bg-muted"
      >
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm">{title}</p>
          <p className={cn('truncate text-xs', tone === 'error' ? 'text-destructive' : 'text-muted-foreground')}>{sub}</p>
        </div>
        <span className="text-muted-foreground group-hover:text-foreground">{icon}</span>
      </button>
    </li>
  );

  return (
    <>
      <DrawerHeader title="Fuentes" onClose={onClose} />
      <p className="px-3 pb-2 text-xs text-muted-foreground">
        Hacé clic en un número de la página o en una fuente para ver su consulta.
      </p>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-3">
        <ul className="flex flex-col">
          {datasets.map((ds) => {
            const s = statusLine(ds, statuses[ds.id]);
            return (
              <Item
                key={ds.id}
                id={ds.id}
                title={ds.title}
                sub={s.text}
                tone={s.tone}
                icon={s.tone === 'loading' ? <LoaderIcon className="size-4 animate-spin" /> : s.tone === 'error' ? <CircleAlertIcon className="size-4 text-destructive" /> : <DatabaseIcon className="size-4" />}
              />
            );
          })}
        </ul>
        {calcs.length > 0 && (
          <>
            <p className="px-2 pt-3 pb-1 text-xs font-medium text-muted-foreground">Cálculos en el navegador</p>
            <ul className="flex flex-col">
              {calcs.map((c) => (
                <Item key={c.id} id={c.id} title={c.id} sub={`de ${c.deps.join(', ')}`} tone="muted" icon={<SigmaIcon className="size-4" />} />
              ))}
            </ul>
          </>
        )}
        {datasets.length === 0 && <Empty text="Todavía no hay fuentes." />}
      </div>
      {!solo && (
        <div className="border-t p-2">
          <Button variant="ghost" size="sm" className="w-full justify-start text-muted-foreground" onClick={() => fileInput.current?.click()}>
            <FileUpIcon /> Adjuntar archivo (CSV, TSV, JSON)
          </Button>
          <input ref={fileInput} type="file" accept=".csv,.tsv,.json" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
          {uploadError && <p className="px-2 pt-1 text-xs text-destructive">{uploadError}</p>}
        </div>
      )}
    </>
  );
}

function Section({ title, defaultOpen = true, children }: { title: string; defaultOpen?: boolean; children: ReactNode }) {
  return (
    <Collapsible defaultOpen={defaultOpen} className="border-t">
      <CollapsibleTrigger className="group flex w-full items-center justify-between px-3 py-2.5 text-sm font-semibold">
        {title}
        <ChevronRightIcon className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
      </CollapsibleTrigger>
      <CollapsibleContent className="px-3 pb-3">{children}</CollapsibleContent>
    </Collapsible>
  );
}

function Field({ label, children, action }: { label: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mt-2 first:mt-0">
      <div className="flex items-center justify-between pb-1">
        <span className="text-xs text-muted-foreground">{label}</span>
        {action}
      </div>
      {children}
    </div>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <TooltipIconButton
      tooltip={copied ? 'Copiado' : label}
      className="size-6"
      onClick={() =>
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        })
      }
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
    </TooltipIconButton>
  );
}

function CodeBlock({ code, sql }: { code: string; sql?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const long = code.split('\n').length > 8;
  return (
    <div className="relative rounded-lg border bg-muted/50">
      <pre
        className={cn(
          'overflow-x-auto p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap',
          long && !expanded && 'max-h-40 overflow-hidden [mask-image:linear-gradient(to_bottom,black_70%,transparent)]',
        )}
      >
        {sql ? highlightSql(code) : code}
      </pre>
      {long && (
        <button type="button" onClick={() => setExpanded((e) => !e)} className="px-3 pb-2 text-xs font-medium text-muted-foreground hover:text-foreground">
          {expanded ? 'Ver menos' : 'Ver más'}
        </button>
      )}
    </div>
  );
}

function SourceDetail({
  id,
  dataset,
  calc,
  status,
  failure,
  onBack,
  onRefresh,
  onClose,
}: {
  id: string;
  dataset?: Dataset;
  calc?: CalcInfo;
  status?: SourceStatus;
  failure?: string;
  onBack: () => void;
  onRefresh: () => void;
  onClose: () => void;
}) {
  if (!dataset && calc) {
    return (
      <>
        <DrawerHeader onBack={onBack} onClose={onClose} />
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="px-3 pb-3">
            <h3 className="text-sm font-semibold">{calc.id}</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Cálculo que corre en el navegador sobre {calc.deps.join(', ')}. La fórmula es visible para cualquier lector.
            </p>
          </div>
          <Section title="Fórmula">
            <Field label="función" action={<CopyButton text={calc.formula} label="Copiar fórmula" />}>
              <CodeBlock code={calc.formula} />
            </Field>
          </Section>
        </div>
      </>
    );
  }
  if (!dataset) return <><DrawerHeader onBack={onBack} onClose={onClose} /><Empty text={`No existe la fuente ${id}.`} /></>;

  const s = statusLine(dataset, status, failure);
  const params = status?.params_used ?? null;
  return (
    <>
      <DrawerHeader
        onBack={onBack}
        onClose={onClose}
        actions={
          dataset.mode === 'live' ? (
            <TooltipIconButton tooltip="Volver a correr esta consulta" onClick={onRefresh}>
              <RefreshCwIcon />
            </TooltipIconButton>
          ) : undefined
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="px-3 pb-3">
          <h3 className="text-sm font-semibold">{dataset.title}</h3>
          {dataset.description && <p className="mt-1 text-sm text-muted-foreground">{dataset.description}</p>}
          <div
            className={cn(
              'mt-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm',
              s.tone === 'error' ? 'border-destructive/40 bg-destructive/5 text-destructive' : 'text-muted-foreground',
            )}
          >
            {s.tone === 'loading' ? <LoaderIcon className="mt-0.5 size-4 shrink-0 animate-spin" /> : s.tone === 'error' ? <CircleAlertIcon className="mt-0.5 size-4 shrink-0" /> : <InfoIcon className="mt-0.5 size-4 shrink-0" />}
            <span className="min-w-0 break-words">
              {MODE_LABEL[dataset.mode]} · {s.text}
            </span>
          </div>
        </div>

        {dataset.sql ? (
          <Section title="Consulta">
            {params && Object.keys(params).length > 0 && (
              <Field label="filtros">
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-lg border px-3 py-2 text-xs">
                  {Object.entries(params).map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className="font-mono text-muted-foreground">{`{{${k}}}`}</dt>
                      <dd className="truncate font-mono">{v == null || v === '' ? 'todos' : Array.isArray(v) ? v.join(', ') : String(v)}</dd>
                    </div>
                  ))}
                </dl>
              </Field>
            )}
            <Field label="query" action={<CopyButton text={dataset.sql} label="Copiar consulta" />}>
              <CodeBlock code={dataset.sql} sql />
            </Field>
            <p className="mt-2 text-xs text-muted-foreground">
              {CONNECTOR[dataset.connector ?? ''] ?? dataset.connector} · <code className="font-mono">{dataset.tool}</code>
            </p>
          </Section>
        ) : (
          <Section title="Origen">
            <p className="text-sm text-muted-foreground">{CONNECTOR.file}. Se actualiza subiendo otra versión del archivo.</p>
          </Section>
        )}

        <Section title="Resultado" defaultOpen={false}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted-foreground">modo</dt>
            <dd>{MODE_LABEL[dataset.mode]}{dataset.mode === 'live' ? ` · caché ${Math.round((dataset.ttl_seconds ?? 3600) / 60)} min` : ''}</dd>
            {dataset.tested_at && (
              <>
                <dt className="text-muted-foreground">probada</dt>
                <dd>{shortDate(dataset.tested_at)}</dd>
              </>
            )}
            {dataset.row_count != null && (
              <>
                <dt className="text-muted-foreground">filas</dt>
                <dd>{dataset.row_count.toLocaleString('es-AR')}</dd>
              </>
            )}
          </dl>
          {dataset.columns && (
            <div className="mt-3 flex flex-wrap gap-1">
              {dataset.columns.map((c) => (
                <span key={c.name} className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px]">
                  {c.name}
                  <span className="text-muted-foreground"> {c.type.toLowerCase()}</span>
                </span>
              ))}
            </div>
          )}
        </Section>
      </div>
    </>
  );
}

function IssuesView({ runs, onClose, onOpenSource }: { runs: RunsSummary | null; onClose: () => void; onOpenSource: (id: string) => void }) {
  const recent = (runs?.recent ?? []).slice().reverse();
  return (
    <>
      <DrawerHeader title="Corridas" onClose={onClose} />
      <p className="px-3 pb-2 text-xs text-muted-foreground">
        Cada apertura registra fuentes que fallaron, valores marcados que no coinciden con los datos y errores de la página.
        El agente las lee para corregir.
      </p>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {recent.length === 0 && <Empty text="Sin corridas todavía." />}
        <ol className="flex flex-col gap-2">
          {recent.map((r, i) => {
            const n = issuesOf(r);
            return (
              <li key={`${r.at}-${i}`} className="rounded-lg border bg-background p-3 text-sm">
                <div className="flex items-center gap-2">
                  {n ? <CircleAlertIcon className="size-4 text-destructive" /> : <CheckIcon className="size-4 text-emerald-600 dark:text-emerald-400" />}
                  <span className="font-medium">{shortDate(r.at)}</span>
                  <span className="text-muted-foreground">{n ? `${n} problema${n > 1 ? 's' : ''}` : 'sin problemas'}</span>
                </div>
                {(n > 0 || r.removed.length > 0) && (
                  <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {r.source_failures.map((f, j) => (
                      <li key={`f${j}`}>
                        <button type="button" className="text-start underline-offset-2 hover:underline" onClick={() => onOpenSource(f.dataset)}>
                          Fuente <code>{f.dataset}</code>
                        </button>
                        : {f.error}
                      </li>
                    ))}
                    {r.mismatches.map((m, j) => (
                      <li key={`m${j}`}>
                        <code>{m.dataset}.{m.field ?? '?'}</code> muestra “{m.shown}”: {m.problem}
                        {m.expected !== undefined ? ` (dato: ${String(m.expected)})` : ''}
                      </li>
                    ))}
                    {r.errors.map((e, j) => (
                      <li key={`e${j}`}>Error de la página: {e.message}{e.line ? ` (línea ${e.line})` : ''}</li>
                    ))}
                    {r.removed.map((x, j) => (
                      <li key={`r${j}`}>Descartado al cargar: {x}</li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </>
  );
}

function CodeView({ page, onClose }: { page: string; onClose: () => void }) {
  return (
    <>
      <DrawerHeader title="Código de la página" onClose={onClose} actions={page ? <CopyButton text={page} label="Copiar código" /> : undefined} />
      <div className="min-h-0 flex-1 overflow-auto px-3 pb-3">
        {page ? <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap">{page}</pre> : <Empty text="Sin página todavía." />}
      </div>
    </>
  );
}
