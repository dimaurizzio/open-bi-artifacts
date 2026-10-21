/** Base de conocimiento: tipos y llamadas a /api/knowledge. */

export type TargetType = 'table' | 'column' | 'metric' | 'term' | 'join';
export type Kind =
  | 'descripcion'
  | 'definicion_metrica'
  | 'regla_filtro'
  | 'advertencia'
  | 'join'
  | 'sinonimo'
  | 'consulta_ejemplo';
export type Visibility = 'private' | 'shared';
export type Status = 'proposed' | 'approved' | 'rejected' | 'deprecated';

export interface Source {
  resource: string;
  title?: string;
}

export interface Entry {
  id: string;
  target_type: TargetType;
  target_ref: string;
  kind: Kind;
  content: string;
  sql: string | null;
  sources: Source[];
  stale_after: string | null;
  author_id: string;
  proposed_via: 'manual' | 'agent';
  source_session_id: string | null;
  visibility: Visibility;
  status: Status;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  supersedes_id: string | null;
  created_at: string;
  provenance: string;
  priority: number;
  stale: boolean;
  can_review: boolean;
  can_deprecate: boolean;
  can_supersede: boolean;
}

export interface CatalogColumn {
  name: string;
  type: string;
  mode?: string;
  description?: string;
}

export interface CatalogTable {
  ref: string;
  dataset: string;
  name: string;
  table_type: string | null;
  description: string | null;
  num_rows: number | null;
  num_bytes: number | null;
  partition_field: string | null;
  partition_type: string | null;
  clustering: string[];
  last_modified: string | null;
  columns: CatalogColumn[];
  synced_at: string;
}

export interface Counts {
  approved: number;
  private: number;
  proposed: number;
  stale: number;
}

export interface Overview {
  user: string;
  curator: boolean;
  tables: (Counts & { ref: string; name: string; dataset: string; description: string; table_type: string | null; num_rows: number | null })[];
  others: (Counts & { ref: string; target_type: TargetType })[];
  pending: number;
  catalog_sync: { at: string; ok: boolean; tables: number; errors: string[] } | null;
  export: { at: string; dir: string; files: number; concepts: number; entries: number } | null;
}

export interface KnowledgeEvent {
  id: number;
  entry_id: string;
  action: string;
  actor: string;
  at: string;
  note: string | null;
}

export interface Draft {
  target_type: TargetType;
  target_ref: string;
  kind: Kind;
  content: string;
  visibility: Visibility;
  sql?: string | null;
  sources?: string[];
  stale_after?: string | null;
  supersedes_id?: string | null;
}

export const KIND_LABELS: Record<Kind, string> = {
  descripcion: 'Descripción',
  definicion_metrica: 'Definición de métrica',
  regla_filtro: 'Regla de filtro',
  advertencia: 'Advertencia',
  join: 'Join',
  sinonimo: 'Sinónimo',
  consulta_ejemplo: 'Consulta de ejemplo',
};
export const NEEDS_SQL: Kind[] = ['definicion_metrica', 'consulta_ejemplo'];
export const TARGET_LABELS: Record<TargetType, string> = {
  table: 'Tabla',
  column: 'Columna',
  metric: 'Métrica',
  term: 'Término',
  join: 'Join',
};
export const STATUS_LABELS: Record<Status, string> = {
  proposed: 'Sin aprobar',
  approved: 'Vigente',
  rejected: 'Rechazada',
  deprecated: 'Deprecada',
};

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body as T;
}

const post = (body: unknown = {}): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });

export const knowledgeApi = {
  overview: () => json<Overview>('/api/knowledge'),
  target: (ref: string) =>
    json<{ target: string; catalog: CatalogTable[]; entries: Entry[] }>(`/api/knowledge/target/${encodeURIComponent(ref)}`),
  entries: (query: { status?: Status; mine?: boolean; all?: boolean; q?: string; target?: string }) => {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== false && v !== '') params.set(k, String(v));
    return json<{ entries: Entry[]; catalog?: CatalogTable[] }>(`/api/knowledge/entries?${params}`);
  },
  propose: (draft: Draft) => json<{ ok: boolean; entry: Entry; warnings: string[] }>('/api/knowledge/entries', post(draft)),
  review: (id: string, action: 'approve' | 'reject', note?: string) =>
    json<{ ok: boolean; entry: Entry; warnings: string[] }>(`/api/knowledge/entries/${id}/review`, post({ action, note })),
  deprecate: (id: string, note?: string) => json<{ ok: boolean; entry: Entry }>(`/api/knowledge/entries/${id}/deprecate`, post({ note })),
  history: (id: string) => json<{ entries: Entry[]; events: KnowledgeEvent[] }>(`/api/knowledge/entries/${id}/history`),
  sync: () => json<{ ok: boolean; tables: number; errors: string[] }>('/api/knowledge/catalog/sync', post()),
  export: () => json<{ files: number; dir: string }>('/api/knowledge/export', post()),
};
