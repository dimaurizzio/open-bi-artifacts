import {
  ArrowLeftIcon,
  BookOpenIcon,
  CheckIcon,
  ChevronRightIcon,
  ClockIcon,
  HistoryIcon,
  LoaderIcon,
  LockIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  TriangleAlertIcon,
  UsersIcon,
  XIcon,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { TooltipIconButton } from '@/components/assistant-ui/tooltip-icon-button';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { ago } from '@/lib/format';
import {
  KIND_LABELS,
  NEEDS_SQL,
  STATUS_LABELS,
  TARGET_LABELS,
  knowledgeApi,
  type CatalogTable,
  type Counts,
  type Draft,
  type Entry,
  type Kind,
  type KnowledgeEvent,
  type Overview,
  type TargetType,
  type Visibility,
} from '@/lib/knowledge';
import { highlightSql } from '@/lib/sql-highlight';
import { useAppStore } from '@/lib/store';
import { cn } from '@/lib/utils';

type Tab = 'tables' | 'concepts' | 'pending' | 'mine';

const fmtInt = (n?: number | null) => (n == null ? '' : n.toLocaleString('es-AR'));
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString('es-AR') : '');

/** Panel Conocimiento: catálogo de BigQuery, conocimiento compartido y notas privadas. */
export function KnowledgePanel({ onClose }: { onClose: () => void }) {
  const initialTarget = useAppStore((s) => s.knowledgeTarget);
  const version = useAppStore((s) => s.knowledgeVersion);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('tables');
  const [target, setTarget] = useState<string | null>(initialTarget);
  const [query, setQuery] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => setTarget(initialTarget), [initialTarget]);

  useEffect(() => {
    knowledgeApi
      .overview()
      .then((o) => {
        setOverview(o);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [tick, version]);

  const sync = async () => {
    setSyncing(true);
    try {
      const r = await knowledgeApi.sync();
      if (r.errors.length) setError(`Sincronizado con errores: ${r.errors[0]}`);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSyncing(false);
    }
  };

  const searching = query.trim().length > 1;

  return (
    <section className="flex h-full min-w-0 flex-col bg-background">
      <header className="flex h-12 shrink-0 items-center gap-1.5 border-b px-3">
        {target || creating ? (
          <TooltipIconButton tooltip="Volver" onClick={() => (creating ? setCreating(false) : setTarget(null))} className="size-8">
            <ArrowLeftIcon />
          </TooltipIconButton>
        ) : (
          <BookOpenIcon className="mx-1 size-4 shrink-0 text-muted-foreground" />
        )}
        <h2 className="min-w-0 flex-1 truncate text-sm font-medium">{target ?? (creating ? 'Nuevo concepto' : 'Conocimiento')}</h2>
        {overview?.curator && (
          <TooltipIconButton tooltip="Sincronizar el catálogo de BigQuery" onClick={sync} disabled={syncing} className="size-8">
            <RefreshCwIcon className={cn(syncing && 'animate-spin')} />
          </TooltipIconButton>
        )}
        <TooltipIconButton tooltip="Cerrar" onClick={onClose} className="size-8">
          <XIcon />
        </TooltipIconButton>
      </header>

      {!target && !creating && (
        <div className="flex flex-col gap-2 border-b px-3 py-2">
          <div className="relative">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Buscar tablas, columnas, métricas, términos…"
              className="ps-8"
              aria-label="Buscar en el conocimiento"
            />
          </div>
          {!searching && (
            <ToggleGroup type="single" value={tab} onValueChange={(v) => v && setTab(v as Tab)} variant="outline" size="sm" className="w-full">
              <ToggleGroupItem value="tables" className="flex-1 text-xs">
                Tablas {overview ? `(${overview.tables.length})` : ''}
              </ToggleGroupItem>
              <ToggleGroupItem value="concepts" className="flex-1 text-xs">
                Métricas y términos {overview ? `(${overview.others.length})` : ''}
              </ToggleGroupItem>
              <ToggleGroupItem value="pending" className="flex-1 text-xs">
                Pendientes {overview?.pending ? `(${overview.pending})` : ''}
              </ToggleGroupItem>
              <ToggleGroupItem value="mine" className="flex-1 text-xs">
                Mías
              </ToggleGroupItem>
            </ToggleGroup>
          )}
        </div>
      )}

      {error && (
        <p className="flex items-start gap-2 border-b bg-destructive/5 px-3 py-2 text-xs text-destructive">
          <TriangleAlertIcon className="mt-px size-3.5 shrink-0" />
          <span className="min-w-0 flex-1">{error}</span>
          <button type="button" className="underline" onClick={() => setError(null)}>
            Cerrar
          </button>
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {creating ? (
          <div className="p-3">
            <EntryForm
              fixedTarget={null}
              onSaved={(e) => {
                setCreating(false);
                setTarget(e.target_ref);
                reload();
              }}
              onCancel={() => setCreating(false)}
            />
          </div>
        ) : target ? (
          <TargetView refName={target} version={tick + version} onChanged={reload} onOpen={setTarget} />
        ) : !overview ? (
          <ListSkeleton />
        ) : searching ? (
          <SearchResults query={query.trim()} version={tick + version} onOpen={setTarget} onChanged={reload} />
        ) : tab === 'tables' ? (
          <TablesList overview={overview} onOpen={setTarget} />
        ) : tab === 'concepts' ? (
          <ConceptsList overview={overview} onOpen={setTarget} onNew={() => setCreating(true)} />
        ) : (
          <EntriesQuery
            key={tab}
            query={tab === 'pending' ? { status: 'proposed' } : { mine: true, all: true }}
            empty={tab === 'pending' ? 'No hay propuestas pendientes.' : 'Todavía no escribiste conocimiento.'}
            version={tick + version}
            onChanged={reload}
            onOpen={setTarget}
          />
        )}
      </div>

      {overview && !target && !creating && (
        <footer className="border-t px-3 py-1.5 text-[11px] text-muted-foreground">
          {overview.catalog_sync ? (
            <>
              Catálogo sincronizado {ago(overview.catalog_sync.at)} · {overview.catalog_sync.tables} tablas
              {overview.catalog_sync.errors.length > 0 && ` · ${overview.catalog_sync.errors.length} errores`}
            </>
          ) : (
            'El catálogo todavía no se sincronizó.'
          )}
          {overview.export && <> · Bundle OKF exportado {ago(overview.export.at)}</>}
        </footer>
      )}
    </section>
  );
}

function ListSkeleton() {
  return (
    <div className="flex flex-col gap-2 p-3">
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}

function CountBadges({ c }: { c: Counts }) {
  return (
    <span className="flex shrink-0 items-center gap-1">
      {c.approved > 0 && (
        <Badge variant="secondary" className="font-normal tabular-nums" title="Compartidas y aprobadas">
          <UsersIcon /> {c.approved}
        </Badge>
      )}
      {c.private > 0 && (
        <Badge variant="outline" className="font-normal tabular-nums" title="Notas privadas tuyas">
          <LockIcon /> {c.private}
        </Badge>
      )}
      {c.proposed > 0 && (
        <Badge variant="outline" className="border-dashed font-normal tabular-nums" title="Propuestas sin aprobar">
          <ClockIcon /> {c.proposed}
        </Badge>
      )}
      {c.stale > 0 && (
        <Badge variant="destructive" className="font-normal tabular-nums" title="Vencidas">
          <TriangleAlertIcon /> {c.stale}
        </Badge>
      )}
    </span>
  );
}

function Row({ onClick, title, subtitle, right }: { onClick: () => void; title: ReactNode; subtitle?: ReactNode; right?: ReactNode }) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-start transition-colors hover:bg-muted"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm">{title}</span>
          {subtitle && <span className="block truncate text-xs text-muted-foreground">{subtitle}</span>}
        </span>
        {right}
        <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" />
      </button>
    </li>
  );
}

function TablesList({ overview, onOpen }: { overview: Overview; onOpen: (ref: string) => void }) {
  const byDataset = useMemo(() => {
    const groups = new Map<string, Overview['tables']>();
    for (const t of overview.tables) groups.set(t.dataset, [...(groups.get(t.dataset) ?? []), t]);
    return [...groups.entries()];
  }, [overview.tables]);
  if (!overview.tables.length) {
    return (
      <p className="p-4 text-sm text-muted-foreground">
        El catálogo está vacío.{' '}
        {overview.curator ? 'Sincronizalo con el botón de arriba.' : 'Pedile a un curador que lo sincronice.'}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3 p-2">
      {byDataset.map(([dataset, tables]) => (
        <section key={dataset}>
          <h3 className="px-2 pb-1 font-mono text-[11px] text-muted-foreground">{dataset}</h3>
          <ul className="flex flex-col">
            {tables.map((t) => (
              <Row
                key={t.ref}
                onClick={() => onOpen(t.ref)}
                title={
                  <>
                    <span className="font-mono">{t.name}</span>
                    {t.table_type && t.table_type !== 'TABLE' && <span className="ms-1.5 text-xs text-muted-foreground">{t.table_type.toLowerCase()}</span>}
                  </>
                }
                subtitle={t.description || (t.num_rows != null ? `${fmtInt(t.num_rows)} filas` : undefined)}
                right={<CountBadges c={t} />}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function ConceptsList({ overview, onOpen, onNew }: { overview: Overview; onOpen: (ref: string) => void; onNew: () => void }) {
  return (
    <div className="flex flex-col gap-2 p-2">
      <Button variant="outline" size="sm" className="mx-1 gap-1.5 self-start" onClick={onNew}>
        <PlusIcon className="size-4" /> Métrica, término o join
      </Button>
      {overview.others.length === 0 ? (
        <p className="px-2 text-sm text-muted-foreground">Todavía no hay métricas, términos ni joins documentados.</p>
      ) : (
        <ul className="flex flex-col">
          {overview.others.map((o) => (
            <Row
              key={o.ref}
              onClick={() => onOpen(o.ref)}
              title={o.ref}
              subtitle={o.target_type === 'table' ? 'Tabla fuera del catálogo: no la usa el agente al consultar' : TARGET_LABELS[o.target_type]}
              right={<CountBadges c={o} />}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function SearchResults({
  query,
  version,
  onOpen,
  onChanged,
}: {
  query: string;
  version: number;
  onOpen: (ref: string) => void;
  onChanged: () => void;
}) {
  const [result, setResult] = useState<{ entries: Entry[]; catalog?: CatalogTable[] } | null>(null);
  useEffect(() => {
    const t = window.setTimeout(() => {
      knowledgeApi
        .entries({ q: query })
        .then(setResult)
        .catch(() => setResult({ entries: [] }));
    }, 250);
    return () => window.clearTimeout(t);
  }, [query, version]);
  if (!result) return <ListSkeleton />;
  const tables = result.catalog ?? [];
  if (!result.entries.length && !tables.length) return <p className="p-4 text-sm text-muted-foreground">Sin resultados.</p>;
  return (
    <div className="flex flex-col gap-3 p-2">
      {tables.length > 0 && (
        <section>
          <h3 className="px-2 pb-1 text-xs font-medium text-muted-foreground">Tablas del catálogo</h3>
          <ul>
            {tables.map((t) => (
              <Row key={t.ref} onClick={() => onOpen(t.ref)} title={<span className="font-mono">{t.name}</span>} subtitle={t.description || t.dataset} />
            ))}
          </ul>
        </section>
      )}
      {result.entries.length > 0 && (
        <section className="flex flex-col gap-2 px-1">
          <h3 className="px-1 text-xs font-medium text-muted-foreground">Conocimiento</h3>
          {result.entries.map((e) => (
            <EntryCard key={e.id} entry={e} onChanged={onChanged} onOpen={onOpen} showTarget />
          ))}
        </section>
      )}
    </div>
  );
}

function EntriesQuery({
  query,
  empty,
  version,
  onChanged,
  onOpen,
}: {
  query: Parameters<typeof knowledgeApi.entries>[0];
  empty: string;
  version: number;
  onChanged: () => void;
  onOpen: (ref: string) => void;
}) {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const key = JSON.stringify(query);
  useEffect(() => {
    knowledgeApi
      .entries(JSON.parse(key))
      .then((r) => setEntries(r.entries))
      .catch(() => setEntries([]));
  }, [key, version]);
  if (!entries) return <ListSkeleton />;
  if (!entries.length) return <p className="p-4 text-sm text-muted-foreground">{empty}</p>;
  return (
    <div className="flex flex-col gap-2 p-3">
      {entries.map((e) => (
        <EntryCard key={e.id} entry={e} onChanged={onChanged} onOpen={onOpen} showTarget />
      ))}
    </div>
  );
}

function TargetView({
  refName,
  version,
  onChanged,
  onOpen,
}: {
  refName: string;
  version: number;
  onChanged: () => void;
  onOpen: (ref: string) => void;
}) {
  const [data, setData] = useState<{ target: string; catalog: CatalogTable[]; entries: Entry[] } | null>(null);
  const [adding, setAdding] = useState<{ type: TargetType; ref: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    knowledgeApi
      .target(refName)
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, [refName, version]);

  if (error) return <p className="p-4 text-sm text-destructive">{error}</p>;
  if (!data) return <ListSkeleton />;

  const table = data.catalog.length === 1 ? data.catalog[0] : null;
  const isTable = !!table || refName.split('.').length >= 3;
  const tableEntries = data.entries.filter((e) => e.target_type !== 'column');
  const columnEntries = data.entries.filter((e) => e.target_type === 'column');
  const perColumn = new Map<string, number>();
  const columnDescriptions = new Map<string, Entry>();
  for (const e of columnEntries) {
    const col = (e.target_ref.split('.').pop() ?? '').toLowerCase();
    perColumn.set(col, (perColumn.get(col) ?? 0) + 1);
    // Las entradas llegan por prioridad: la primera descripción vigente de cada columna gana.
    if (e.kind === 'descripcion' && !e.stale && !columnDescriptions.has(col)) columnDescriptions.set(col, e);
  }
  const tableDescription = tableEntries.find((e) => e.kind === 'descripcion' && !e.stale) ?? null;
  const defaultType: TargetType = isTable ? 'table' : (data.entries[0]?.target_type ?? 'term');

  return (
    <div className="flex flex-col gap-4 p-3">
      {data.catalog.length > 1 && (
        <div className="rounded-lg border p-3 text-sm">
          <p className="pb-1.5 text-muted-foreground">Hay varias tablas con ese nombre:</p>
          <ul>
            {data.catalog.map((t) => (
              <Row key={t.ref} onClick={() => onOpen(t.ref)} title={<span className="font-mono">{t.ref}</span>} />
            ))}
          </ul>
        </div>
      )}

      {table && (
        <CatalogCard
          table={table}
          perColumn={perColumn}
          description={tableDescription}
          columnDescriptions={columnDescriptions}
          onAddColumn={(c) => setAdding({ type: 'column', ref: `${table.ref}.${c}` })}
        />
      )}

      <section className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <h3 className="flex-1 text-sm font-medium">Conocimiento</h3>
          {!adding && (
            <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setAdding({ type: defaultType, ref: data.target })}>
              <PlusIcon className="size-4" /> Agregar
            </Button>
          )}
        </div>
        {adding && (
          <EntryForm
            fixedTarget={adding}
            columns={table?.columns.map((c) => c.name)}
            tableRef={table?.ref}
            onSaved={() => {
              setAdding(null);
              onChanged();
            }}
            onCancel={() => setAdding(null)}
          />
        )}
        {data.entries.length === 0 && !adding && (
          <p className="text-sm text-muted-foreground">Nada guardado todavía. Lo que sepas de {isTable ? 'esta tabla' : 'esto'} ayuda al agente a no adivinar.</p>
        )}
        {tableEntries.map((e) => (
          <EntryCard key={e.id} entry={e} onChanged={onChanged} onOpen={onOpen} />
        ))}
        {columnEntries.length > 0 && <h4 className="pt-2 text-xs font-medium text-muted-foreground">Columnas</h4>}
        {columnEntries.map((e) => (
          <EntryCard key={e.id} entry={e} onChanged={onChanged} onOpen={onOpen} showTarget />
        ))}
      </section>
    </div>
  );
}

function CatalogCard({
  table,
  perColumn,
  description,
  columnDescriptions,
  onAddColumn,
}: {
  table: CatalogTable;
  perColumn: Map<string, number>;
  /** Descripción guardada como conocimiento: se muestra si BigQuery no tiene una. */
  description: Entry | null;
  columnDescriptions: Map<string, Entry>;
  onAddColumn: (column: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const facts = [
    table.table_type && table.table_type !== 'TABLE' ? table.table_type.toLowerCase() : null,
    table.num_rows != null ? `${fmtInt(table.num_rows)} filas` : null,
    table.partition_field || table.partition_type ? `partición ${table.partition_field ?? '_PARTITIONTIME'} (${table.partition_type ?? 'DAY'})` : null,
    table.clustering.length ? `clustering ${table.clustering.join(', ')}` : null,
    table.last_modified ? `actualizada ${day(table.last_modified)}` : null,
  ].filter(Boolean);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-xl border bg-card text-card-foreground">
      <div className="flex flex-col gap-1 px-3 pt-2.5 pb-2">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="font-normal text-muted-foreground">
            Catálogo de BigQuery
          </Badge>
          <span className="text-[11px] text-muted-foreground">sincronizado {ago(table.synced_at)}</span>
        </div>
        <p className="font-mono text-xs break-all text-muted-foreground">{table.ref}</p>
        {table.description ? (
          <p className="text-sm">{table.description}</p>
        ) : description ? (
          <p className="text-sm" title={description.provenance}>
            {description.content}
            <span className="ms-1.5 text-[11px] text-muted-foreground">{description.status === 'approved' || description.visibility === 'private' ? '· del conocimiento' : '· propuesta sin aprobar'}</span>
          </p>
        ) : (
          <p className="text-sm text-muted-foreground italic">Sin descripción: agregala abajo, en Conocimiento.</p>
        )}
        {facts.length > 0 && <p className="text-xs text-muted-foreground">{facts.join(' · ')}</p>}
      </div>
      <CollapsibleTrigger className="flex w-full items-center gap-1.5 border-t px-3 py-1.5 text-start text-xs text-muted-foreground hover:bg-muted/50">
        <ChevronRightIcon className={cn('size-3.5 transition-transform', open && 'rotate-90')} />
        {table.columns.length} columnas
      </CollapsibleTrigger>
      <CollapsibleContent className="max-h-80 overflow-auto border-t">
        <table className="w-full text-xs">
          <tbody>
            {table.columns.map((c) => (
              <tr key={c.name} className="group border-b last:border-0">
                <td className="py-1 ps-3 pe-2 align-top font-mono">{c.name}</td>
                <td className="py-1 pe-2 align-top text-muted-foreground">{c.type}</td>
                <td className="py-1 pe-2 align-top text-muted-foreground" title={c.description ? undefined : columnDescriptions.get(c.name.toLowerCase())?.provenance}>
                  {c.description || columnDescriptions.get(c.name.toLowerCase())?.content}
                </td>
                <td className="w-16 py-1 pe-2 text-end align-top">
                  {perColumn.get(c.name.toLowerCase()) ? (
                    <Badge variant="secondary" className="font-normal">
                      {perColumn.get(c.name.toLowerCase())}
                    </Badge>
                  ) : null}
                  <button
                    type="button"
                    title={`Agregar conocimiento sobre ${c.name}`}
                    onClick={() => onAddColumn(c.name)}
                    className="ms-1 inline-flex size-5 items-center justify-center rounded text-muted-foreground opacity-0 group-hover:opacity-100 hover:bg-muted focus-visible:opacity-100"
                  >
                    <PlusIcon className="size-3.5" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CollapsibleContent>
    </Collapsible>
  );
}

function EntryCard({
  entry: e,
  onChanged,
  onOpen,
  showTarget,
}: {
  entry: Entry;
  onChanged: () => void;
  onOpen: (ref: string) => void;
  showTarget?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [asking, setAsking] = useState<'reject' | 'deprecate' | null>(null);
  const [note, setNote] = useState('');
  const [correcting, setCorrecting] = useState(false);
  const [history, setHistory] = useState<{ entries: Entry[]; events: KnowledgeEvent[] } | null>(null);
  const inactive = e.status === 'rejected' || e.status === 'deprecated';

  const act = async (fn: () => Promise<{ ok: boolean; warnings?: string[] }>) => {
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      setWarnings(r.warnings ?? []);
      setAsking(null);
      setNote('');
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleHistory = async () => {
    if (history) return setHistory(null);
    try {
      setHistory(await knowledgeApi.history(e.id));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const tableOf = (ref: string) => (e.target_type === 'column' ? ref.split('.').slice(0, -1).join('.') : ref);

  return (
    <article
      className={cn(
        'rounded-xl border bg-card text-card-foreground',
        e.status === 'proposed' && 'border-dashed',
        inactive && 'opacity-70',
      )}
    >
      <div className="flex flex-col gap-1.5 px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={e.kind === 'advertencia' ? 'destructive' : 'secondary'} className="font-normal">
            {KIND_LABELS[e.kind]}
          </Badge>
          {e.visibility === 'private' ? (
            <Badge variant="outline" className="font-normal">
              <LockIcon /> Privada
            </Badge>
          ) : (
            e.status !== 'approved' && (
              <Badge variant="outline" className="font-normal">
                {STATUS_LABELS[e.status]}
              </Badge>
            )
          )}
          {e.stale && (
            <Badge variant="destructive" className="font-normal">
              <TriangleAlertIcon /> Vencida
            </Badge>
          )}
          {showTarget && (
            <button
              type="button"
              onClick={() => onOpen(tableOf(e.target_ref))}
              className="min-w-0 truncate font-mono text-[11px] text-muted-foreground hover:text-foreground hover:underline"
              title={e.target_ref}
            >
              {e.target_type === 'column' ? e.target_ref.split('.').slice(-2).join('.') : e.target_ref}
            </button>
          )}
        </div>
        <p className="text-sm whitespace-pre-wrap">{e.content}</p>
        {e.sql && (
          <pre className="max-h-48 overflow-auto rounded-md bg-muted/50 px-2.5 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap">
            {highlightSql(e.sql)}
          </pre>
        )}
        {e.sources.length > 0 && (
          <ul className="flex flex-col gap-0.5 text-xs text-muted-foreground">
            {e.sources.map((s, i) => (
              <li key={i} className="truncate">
                Fuente:{' '}
                {/^https?:\/\//.test(s.resource) ? (
                  <a href={s.resource} target="_blank" rel="noreferrer noopener" className="underline underline-offset-2 hover:text-foreground">
                    {s.title || s.resource}
                  </a>
                ) : (
                  s.title || s.resource
                )}
              </li>
            ))}
          </ul>
        )}
        <p className="text-[11px] text-muted-foreground">
          {e.provenance}
          {e.stale_after && !e.stale && ` · vence el ${e.stale_after}`}
          {e.review_note && ` · nota de revisión: ${e.review_note}`}
        </p>
        {warnings.map((w) => (
          <p key={w} className="text-xs text-amber-600 dark:text-amber-400">
            {w}
          </p>
        ))}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>

      {asking && (
        <div className="flex flex-col gap-2 border-t px-3 py-2">
          <Input
            value={note}
            onChange={(ev) => setNote(ev.target.value)}
            placeholder={asking === 'reject' ? 'Motivo del rechazo (opcional)' : 'Por qué se depreca (opcional)'}
            autoFocus
          />
          <div className="flex justify-end gap-1.5">
            <Button variant="ghost" size="sm" onClick={() => setAsking(null)}>
              Cancelar
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={busy}
              onClick={() =>
                act(() => (asking === 'reject' ? knowledgeApi.review(e.id, 'reject', note) : knowledgeApi.deprecate(e.id, note)))
              }
            >
              {asking === 'reject' ? 'Rechazar' : 'Deprecar'}
            </Button>
          </div>
        </div>
      )}

      {correcting && (
        <div className="border-t p-3">
          <EntryForm
            fixedTarget={{ type: e.target_type, ref: e.target_ref }}
            supersedes={e}
            onSaved={() => {
              setCorrecting(false);
              onChanged();
            }}
            onCancel={() => setCorrecting(false)}
          />
        </div>
      )}

      {history && (
        <div className="flex flex-col gap-1 border-t px-3 py-2 text-xs">
          {history.events.map((ev) => (
            <p key={ev.id} className="text-muted-foreground">
              <span className="tabular-nums">{day(ev.at)}</span> · {eventLabel(ev.action)} por {ev.actor}
              {ev.entry_id !== e.id && <span className="font-mono"> ({ev.entry_id})</span>}
              {ev.note && ` · ${ev.note}`}
            </p>
          ))}
          {history.entries
            .filter((x) => x.id !== e.id)
            .map((x) => (
              <p key={x.id} className="rounded-md bg-muted/50 px-2 py-1">
                <span className="text-muted-foreground">{STATUS_LABELS[x.status]} · </span>
                {x.content}
              </p>
            ))}
        </div>
      )}

      {!correcting && !asking && (
        <div className="flex flex-wrap items-center gap-1 border-t px-2 py-1">
          {e.can_review && (
            <>
              <Button size="sm" variant="ghost" className="h-7 gap-1 px-2" disabled={busy} onClick={() => act(() => knowledgeApi.review(e.id, 'approve'))}>
                {busy ? <LoaderIcon className="size-3.5 animate-spin" /> : <CheckIcon className="size-3.5" />} Aprobar
              </Button>
              <Button size="sm" variant="ghost" className="h-7 gap-1 px-2" disabled={busy} onClick={() => setAsking('reject')}>
                <XIcon className="size-3.5" /> Rechazar
              </Button>
            </>
          )}
          {e.can_supersede && (
            <Button size="sm" variant="ghost" className="h-7 gap-1 px-2" onClick={() => setCorrecting(true)}>
              <PencilIcon className="size-3.5" /> Corregir
            </Button>
          )}
          {e.can_deprecate && (
            <Button size="sm" variant="ghost" className="h-7 gap-1 px-2" onClick={() => setAsking('deprecate')}>
              Deprecar
            </Button>
          )}
          <Button size="sm" variant="ghost" className="ms-auto h-7 gap-1 px-2 text-muted-foreground" onClick={toggleHistory}>
            <HistoryIcon className="size-3.5" /> Historia
          </Button>
        </div>
      )}
    </article>
  );
}

function eventLabel(action: string) {
  return (
    {
      created: 'Creada',
      proposed: 'Propuesta',
      approved: 'Aprobada',
      rejected: 'Rechazada',
      deprecated: 'Deprecada',
      superseded: 'Reemplazada',
    }[action] ?? action
  );
}

const KINDS = Object.keys(KIND_LABELS) as Kind[];
const selectClass =
  'h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30';

/** Alta o corrección de una entrada. Una corrección mantiene el objetivo y la visibilidad. */
function EntryForm({
  fixedTarget,
  columns,
  tableRef,
  supersedes,
  onSaved,
  onCancel,
}: {
  fixedTarget: { type: TargetType; ref: string } | null;
  columns?: string[];
  tableRef?: string;
  supersedes?: Entry;
  onSaved: (e: Entry) => void;
  onCancel: () => void;
}) {
  const [targetType, setTargetType] = useState<TargetType>(fixedTarget?.type ?? 'metric');
  const [targetRef, setTargetRef] = useState(fixedTarget?.ref ?? '');
  const [kind, setKind] = useState<Kind>(supersedes?.kind ?? (fixedTarget?.type === 'metric' ? 'definicion_metrica' : 'descripcion'));
  const [content, setContent] = useState(supersedes?.content ?? '');
  const [sql, setSql] = useState(supersedes?.sql ?? '');
  const [sources, setSources] = useState((supersedes?.sources ?? []).map((s) => s.resource).join('\n'));
  const [staleAfter, setStaleAfter] = useState(supersedes?.stale_after ?? '');
  const [visibility, setVisibility] = useState<Visibility>(supersedes?.visibility ?? 'private');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  // Sobre una tabla se puede elegir la tabla o una de sus columnas.
  const canPickColumn = !supersedes && !!tableRef && !!columns?.length;
  const column = targetType === 'column' && tableRef ? targetRef.slice(tableRef.length + 1) : '';
  const needsSql = NEEDS_SQL.includes(kind);

  const submit = async () => {
    setBusy(true);
    setError(null);
    const draft: Draft = {
      target_type: targetType,
      target_ref: targetRef.trim(),
      kind,
      content: content.trim(),
      visibility,
      sql: sql.trim() || null,
      sources: sources
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
      stale_after: staleAfter || null,
      supersedes_id: supersedes?.id ?? null,
    };
    try {
      const r = await knowledgeApi.propose(draft);
      setWarnings(r.warnings);
      onSaved(r.entry);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="flex flex-col gap-2.5 rounded-xl border bg-muted/30 p-3"
      onSubmit={(ev) => {
        ev.preventDefault();
        void submit();
      }}
    >
      {supersedes && <p className="text-xs text-muted-foreground">La corrección reemplaza a la entrada actual {supersedes.visibility === 'shared' ? 'cuando un curador la apruebe' : 'al guardarla'}. La anterior queda en la historia.</p>}

      {!fixedTarget && (
        <div className="grid grid-cols-[8rem_1fr] gap-2">
          <select className={selectClass} value={targetType} onChange={(e) => setTargetType(e.target.value as TargetType)} aria-label="Tipo de concepto">
            <option value="metric">Métrica</option>
            <option value="term">Término</option>
            <option value="join">Join</option>
            <option value="table">Tabla</option>
          </select>
          <Input value={targetRef} onChange={(e) => setTargetRef(e.target.value)} placeholder={targetType === 'table' ? 'proyecto.dataset.tabla' : 'Nombre, p. ej. churn mensual'} required />
        </div>
      )}

      {canPickColumn && (
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Sobre
          <select
            className={selectClass}
            value={targetType === 'column' ? column : ''}
            onChange={(e) => {
              const c = e.target.value;
              setTargetType(c ? 'column' : 'table');
              setTargetRef(c ? `${tableRef}.${c}` : tableRef!);
            }}
          >
            <option value="">La tabla entera</option>
            {columns!.map((c) => (
              <option key={c} value={c}>
                Columna {c}
              </option>
            ))}
          </select>
        </label>
      )}

      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Tipo
        <select className={selectClass} value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {KIND_LABELS[k]}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Contenido
        <Textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder={kind === 'regla_filtro' ? 'Siempre excluir el tráfico de bots (flag_bot = TRUE).' : 'Qué hay que saber, en una o dos oraciones.'}
          rows={3}
          required
          className="text-sm text-foreground"
        />
      </label>

      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        SQL {needsSql ? '(obligatorio)' : '(opcional)'}
        <Textarea
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          placeholder="SELECT … (solo lectura, sin parámetros)"
          rows={needsSql ? 4 : 2}
          required={needsSql}
          className="font-mono text-xs text-foreground"
        />
      </label>

      <div className="grid grid-cols-[1fr_9rem] gap-2">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Fuentes (una por línea)
          <Textarea value={sources} onChange={(e) => setSources(e.target.value)} rows={1} placeholder="https://… o un documento" className="text-xs text-foreground" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Vence el
          <Input type="date" value={staleAfter} onChange={(e) => setStaleAfter(e.target.value)} className="text-xs" />
        </label>
      </div>

      {!supersedes && (
        <ToggleGroup type="single" value={visibility} onValueChange={(v) => v && setVisibility(v as Visibility)} variant="outline" size="sm" className="w-full">
          <ToggleGroupItem value="private" className="flex-1 gap-1.5 text-xs">
            <LockIcon className="size-3.5" /> Nota privada
          </ToggleGroupItem>
          <ToggleGroupItem value="shared" className="flex-1 gap-1.5 text-xs">
            <UsersIcon className="size-3.5" /> Compartido (con aprobación)
          </ToggleGroupItem>
        </ToggleGroup>
      )}

      {warnings.map((w) => (
        <p key={w} className="text-xs text-amber-600 dark:text-amber-400">
          {w}
        </p>
      ))}
      {error && <p className="text-xs text-destructive">{error}</p>}

      <div className="flex justify-end gap-1.5">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Cancelar
        </Button>
        <Button type="submit" size="sm" disabled={busy} className="gap-1.5">
          {busy && <LoaderIcon className="size-3.5 animate-spin" />}
          {supersedes ? 'Guardar corrección' : visibility === 'shared' ? 'Proponer' : 'Guardar'}
        </Button>
      </div>
    </form>
  );
}
