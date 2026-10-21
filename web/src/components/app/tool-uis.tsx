import { makeAssistantDataUI, makeAssistantToolUI, useAuiState } from '@assistant-ui/react';
import { BookOpenIcon, ChevronRightIcon, DatabaseIcon, LoaderIcon, TriangleAlertIcon } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ArtifactCard } from '@/components/assistant-ui/elements/artifact-card';
import { DataTable, type DataTableColumn, type DataTableRow } from '@/components/assistant-ui/elements/data-table';
import { Badge } from '@/components/ui/badge';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { api } from '@/lib/api';
import { shortDate } from '@/lib/format';
import { highlightSql } from '@/lib/sql-highlight';
import { useAppStore } from '@/lib/store';
import type { ArtifactMeta } from '@/lib/types';
import { cn } from '@/lib/utils';

type SqlArgs = { query?: string; dryRun?: boolean };
type SqlResult = {
  columns?: { name: string; type: string }[];
  rows?: Record<string, unknown>[];
  total_rows?: number;
  bytes_processed?: number;
  nota?: string;
  error?: string;
  dry_run?: boolean;
};

const fmtBytes = (n?: number) =>
  n == null ? '' : n < 1024 ** 2 ? `${(n / 1024).toFixed(0)} KB` : n < 1024 ** 3 ? `${(n / 1024 ** 2).toFixed(1)} MB` : `${(n / 1024 ** 3).toFixed(2)} GB`;

/** Formato de cada columna según el tipo de BigQuery. */
function columnOf(c: { name: string; type: string }): DataTableColumn {
  const t = c.type.toUpperCase();
  if (['INTEGER', 'INT64', 'FLOAT', 'FLOAT64', 'NUMERIC', 'BIGNUMERIC'].includes(t)) {
    return { key: c.name, label: c.name, align: 'end', format: { kind: 'number', decimals: t.startsWith('INT') ? 0 : 2 } };
  }
  if (['DATE', 'DATETIME', 'TIMESTAMP'].includes(t)) return { key: c.name, label: c.name, format: { kind: 'date', style: t === 'DATE' ? 'date' : 'datetime' } };
  if (t === 'BOOLEAN' || t === 'BOOL') return { key: c.name, label: c.name, format: { kind: 'boolean', trueLabel: 'sí', falseLabel: 'no' } };
  return { key: c.name, label: c.name };
}

function toRow(r: Record<string, unknown>): DataTableRow {
  const out: Record<string, DataTableRow[string]> = {};
  for (const [k, v] of Object.entries(r)) {
    out[k] = v == null || typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean' ? (v as never) : JSON.stringify(v);
  }
  return out;
}

/** Consulta SQL: estado, filas y bytes; al abrirla, el SQL y el resultado en una tabla ordenable. */
export const SqlToolUI = makeAssistantToolUI<SqlArgs, SqlResult | string>({
  toolName: 'execute_sql_readonly',
  render: ({ args, result, status, isError }) => {
    const [open, setOpen] = useState(false);
    const running = status.type === 'running';
    const res = typeof result === 'object' && result ? result : undefined;
    const failed = isError || !!res?.error || status.type === 'incomplete';
    const rows = res?.rows ?? [];
    const cols = res?.columns ?? [];
    const dry = args.dryRun || res?.dry_run;
    return (
      <Collapsible open={open} onOpenChange={setOpen} className="my-1.5 overflow-hidden rounded-xl border bg-card text-card-foreground">
        <CollapsibleTrigger className="flex w-full items-center gap-2 px-3 py-2 text-start text-sm hover:bg-muted/50">
          {running ? (
            <LoaderIcon className="size-4 shrink-0 animate-spin text-muted-foreground" />
          ) : failed ? (
            <TriangleAlertIcon className="size-4 shrink-0 text-destructive" />
          ) : (
            <DatabaseIcon className="size-4 shrink-0 text-muted-foreground" />
          )}
          <span className={cn('font-medium', running && 'shimmer')}>
            {running ? 'Consultando BigQuery' : failed ? 'La consulta falló' : dry ? 'Estimación de costo (dry run)' : 'Consulta SQL'}
          </span>
          {res && !failed && !dry && res.total_rows != null && (
            <Badge variant="secondary" className="font-normal tabular-nums">
              {res.total_rows.toLocaleString('es-AR')} filas
            </Badge>
          )}
          {res?.bytes_processed != null && !failed && (
            <span className="text-xs text-muted-foreground tabular-nums">{fmtBytes(res.bytes_processed)} escaneados</span>
          )}
          <ChevronRightIcon className={cn('ms-auto size-4 text-muted-foreground transition-transform', open && 'rotate-90')} />
        </CollapsibleTrigger>
        <CollapsibleContent className="border-t">
          <pre className="max-h-64 overflow-auto bg-muted/40 px-3 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap">
            {highlightSql(args.query ?? '')}
          </pre>
          {failed && (
            <p className="border-t px-3 py-2 text-sm text-destructive">
              {res?.error ?? (typeof result === 'string' ? result : 'Error sin detalle')}
            </p>
          )}
          {!failed && cols.length > 0 && (
            <div className="max-h-80 overflow-auto border-t p-2">
              <DataTable columns={cols.map(columnOf)} rows={rows.map(toRow)} locale="es-AR" emptyMessage="Sin filas" />
              {res?.nota && <p className="px-1 pt-1.5 text-xs text-muted-foreground">{res.nota}</p>}
            </div>
          )}
        </CollapsibleContent>
      </Collapsible>
    );
  },
});

/** Tarjeta del artifact que el agente creó o editó en este turno. */
export const ArtifactDataUI = makeAssistantDataUI<{ artifactId: string }>({
  name: 'artifact',
  render: ({ data }) => {
    const [meta, setMeta] = useState<ArtifactMeta | null>(null);
    const version = useAppStore((s) => s.artifactVersion);
    const openArtifact = useAppStore((s) => s.openArtifact);
    const writing = useAuiState((s) => s.message.status?.type === 'running');
    useEffect(() => {
      api.getArtifact(data.artifactId).then((d) => setMeta(d.meta)).catch(() => setMeta(null));
    }, [data.artifactId, version]);
    const kind = meta?.kind === 'analysis' ? 'Análisis' : 'Dashboard';
    return (
      <div className="my-3">
        <ArtifactCard
          role="button"
          tabIndex={0}
          title={meta?.title ?? 'Artifact'}
          meta={meta ? `${kind} · editado ${shortDate(meta.updated_at)}` : data.artifactId}
          generating={writing}
          onClick={() => openArtifact(data.artifactId)}
          onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && openArtifact(data.artifactId)}
        />
      </div>
    );
  },
});

type ProposeArgs = { draft_id?: string };
type ProposeResult = {
  ok?: boolean;
  error?: string;
  draft_id?: string;
  borrador?: { sobre: string; tipo: string; contenido: string; sql?: string | null; visibilidad: string; vence?: string | null; corrige?: string | null };
  guardado?: string;
  procedencia?: string;
  estado?: string;
  advertencias?: string[];
};

/** Borrador o guardado de conocimiento: qué quedaría y, una vez guardado, con qué procedencia. */
export const KnowledgeProposeUI = makeAssistantToolUI<ProposeArgs, ProposeResult | string>({
  toolName: 'knowledge_propose',
  render: ({ result, status }) => {
    const openKnowledge = useAppStore((s) => s.openKnowledge);
    const res = typeof result === 'object' && result ? result : undefined;
    const running = status.type === 'running';
    const failed = status.type === 'incomplete' || (res && res.ok === false);
    const d = res?.borrador;
    return (
      <div className="my-1.5 overflow-hidden rounded-xl border bg-card text-card-foreground">
        <div className="flex items-center gap-2 px-3 py-2 text-sm">
          {running ? (
            <LoaderIcon className="size-4 shrink-0 animate-spin text-muted-foreground" />
          ) : failed ? (
            <TriangleAlertIcon className="size-4 shrink-0 text-destructive" />
          ) : (
            <BookOpenIcon className="size-4 shrink-0 text-muted-foreground" />
          )}
          <span className={cn('font-medium', running && 'shimmer')}>
            {running ? 'Preparando conocimiento' : failed ? 'No se pudo' : res?.guardado ? 'Conocimiento guardado' : 'Borrador de conocimiento (sin guardar)'}
          </span>
          {res?.guardado && (
            <button type="button" onClick={() => openKnowledge()} className="ms-auto text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground">
              Ver en Conocimiento
            </button>
          )}
        </div>
        {failed && res?.error && <p className="border-t px-3 py-2 text-sm text-destructive">{res.error}</p>}
        {d && (
          <div className="flex flex-col gap-1 border-t px-3 py-2 text-sm">
            <p className="text-xs text-muted-foreground">
              {d.tipo} · <span className="font-mono">{d.sobre}</span> · {d.visibilidad}
              {d.vence ? ` · vence el ${d.vence}` : ''}
              {d.corrige ? ` · corrige ${d.corrige}` : ''}
            </p>
            <p className="whitespace-pre-wrap">{d.contenido}</p>
            {d.sql && <pre className="max-h-40 overflow-auto rounded-md bg-muted/50 px-2 py-1.5 font-mono text-xs whitespace-pre-wrap">{highlightSql(d.sql)}</pre>}
          </div>
        )}
        {res?.guardado && (
          <p className="border-t px-3 py-2 text-xs text-muted-foreground">
            {res.procedencia} · {res.estado}
          </p>
        )}
        {res?.advertencias?.map((w) => (
          <p key={w} className="border-t px-3 py-1.5 text-xs text-amber-600 dark:text-amber-400">
            {w}
          </p>
        ))}
      </div>
    );
  },
});

export function ToolUIs() {
  return (
    <>
      <SqlToolUI />
      <ArtifactDataUI />
      <KnowledgeProposeUI />
    </>
  );
}
