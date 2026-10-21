// Mensajes que emite el servidor (un Event de ADK traducido) y estructuras de la API.

export type StreamItem =
  | { type: 'text'; role: 'user' | 'assistant'; text: string; partial: boolean; model?: string | null }
  | { type: 'tool_call'; id: string | null; name: string; args: Record<string, unknown>; model?: string | null }
  | { type: 'tool_result'; id: string | null; name: string; result: unknown; ok: boolean }
  | { type: 'artifact'; artifact_id: string; tool: string }
  | { type: 'error'; message: string }
  | { type: 'cancelled' }
  | { type: 'done' };

export interface Suggestion {
  title: string;
  label?: string;
  prompt: string;
}

export interface ChatSummary {
  id: string;
  title: string;
  archived?: boolean;
  running?: boolean;
  updated: number;
}

export interface ArtifactMeta {
  id: string;
  kind: 'dashboard' | 'analysis';
  contract?: 2;
  title: string;
  description: string;
  owner: string;
  project_id?: string | null;
  editable?: boolean;
  created_at: string;
  updated_at: string;
}

export interface Dataset {
  id: string;
  title: string;
  description?: string;
  mode: 'live' | 'saved' | 'file';
  connector?: string;
  tool?: string | null;
  sql?: string | null;
  params?: string[];
  ttl_seconds?: number;
  columns?: { name: string; type: string }[];
  tested_at?: string;
  row_count?: number;
}

export interface RunRecord {
  at: string;
  viewer: string;
  datasets_loaded: string[];
  source_failures: { dataset: string; error: string }[];
  mismatches: { dataset: string; field?: string; shown: string; expected?: unknown; problem: string }[];
  errors: { message: string; line?: number }[];
  removed: string[];
  unmarked_numbers: number;
}

export interface RunsSummary {
  total_runs: number;
  recent: RunRecord[];
  status: string;
}

export interface ArtifactDetail {
  meta: ArtifactMeta;
  params: { name: string; label?: string; type: string }[];
  datasets: Record<string, Dataset>;
  docs: string[];
  page: string;
  runs: RunsSummary;
}

// ---------------------------------------------------------------- contrato 2

export interface QueryCall {
  connector: string;
  tool: string;
  args: Record<string, unknown>;
}

export interface DashDataset {
  title: string;
  description?: string;
  source: ({ kind: 'query' } & QueryCall) | { kind: 'file'; url: string; name: string; format?: string } | { kind: string; [k: string]: unknown };
  updated?: { at: string; by: string };
  request?: QueryCall;
  lastAttempt?: { at: string; ok: boolean; error?: string; by?: string };
  schedule?: string;
  staleAfterHours?: number;
}

export interface DashRunsDoc {
  params?: Record<string, unknown>;
  datasets?: Record<string, { status: string; message?: string; rows?: number; cut?: boolean; ms?: number; at?: string }>;
  marks?: { mark: string; source: string; field?: string; where?: string; row?: number; shown?: string; inData?: unknown; reason?: string }[];
  flagged?: number;
  errors?: { kind: string; name: string; message: string; file?: string; count: number }[];
}

export interface DashDetail {
  contract: 2;
  meta: { id: string; title: string; owner: string; created_at: string; updated_at: string };
  params: Record<string, unknown>;
  datasets: Record<string, DashDataset>;
  unused: string[];
  files: Record<string, string>;
  runs: DashRunsDoc | null;
  editor: boolean;
  project_id: string | null;
}

export interface DashLoad {
  data?: unknown;
  answer?: unknown;
  columns?: string[];
  meta?: MetaItem[];
  cut?: boolean;
  rows?: number;
  at?: string;
  stale?: boolean;
  args?: Record<string, unknown>;
  notice?: string;
}

export interface MetaItem {
  label: string;
  value: string;
  type?: 'text' | 'code';
  lang?: string;
}

/** Estado de una fuente según la página del lector (lo manda el visor). */
export interface SourceState {
  status: 'loading' | 'ok' | 'error' | 'missing' | 'connect' | 'declined' | 'idle';
  message?: string;
  refreshing?: boolean;
  at?: string;
  stale?: boolean;
  rows?: number;
  cut?: boolean;
  ms?: number;
  failure?: string;
}

export interface CalcState {
  id: string;
  title: string;
  description: string;
  inputs: string[];
  status: string;
  error?: string;
}

export interface Inspection {
  id: string;
  kind: 'query' | 'file' | 'calc' | 'missing';
  status: string;
  message?: string;
  data?: unknown;
  columns?: string[];
  meta?: MetaItem[];
  at?: string;
  stale?: boolean;
  rows?: number;
  cut?: boolean;
  args?: Record<string, unknown>;
  values?: Record<string, unknown>;
  loader?: { description: string; fn: string } | null;
  title?: string;
  description?: string;
  inputs?: string[];
  fn?: string;
}

/** Lo que se clickeó en modo Datos (o se seleccionó en la página). */
export interface Selection {
  id: string;
  ids: string[];
  at?: string;
  field?: string;
  where?: string;
  row?: number;
  rowIndex?: number;
  shown?: string;
  value?: unknown;
}

export interface TestResult {
  data: Record<string, unknown>[];
  columns: string[];
  rows: number;
  cut: boolean;
  meta: MetaItem[];
  ms: number;
  at: string;
}

export interface SourceVersion {
  version: string;
  at: string;
  data: DashDataset;
}

export interface CalcInfo {
  id: string;
  deps: string[];
  formula: string;
}
