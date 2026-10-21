import type {
  ArtifactDetail,
  ArtifactMeta,
  ChatSummary,
  DashDetail,
  DashLoad,
  SourceVersion,
  StreamItem,
  Suggestion,
  TestResult,
} from './types';
import { clientTime } from './greeting';
import { modelChoice } from './models';
import type { ViewContext } from './view-context';

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: string,
  ) {
    super(message);
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(body.detail || `HTTP ${res.status}`, body.status);
  return body as T;
}

const post = (body: unknown, method = 'POST'): RequestInit => ({ method, body: JSON.stringify(body) });

/** Contrato 2: lo que la página pide y lo que Fuentes edita. */
export const dashApi = {
  get: (id: string) => json<DashDetail>(`/api/artifacts/${id}`),
  load: (id: string, ds: string, body: { values: Record<string, unknown>; cache: string; raw: boolean }) =>
    json<DashLoad>(`/api/dash/${id}/load/${encodeURIComponent(ds)}`, post(body)),
  call: (id: string, ds: string, tool: string, args: Record<string, unknown>) =>
    json<unknown>(`/api/dash/${id}/call/${encodeURIComponent(ds)}`, post({ tool, args })),
  putRuns: (id: string, doc: unknown) => json<{ ok: boolean }>(`/api/dash/${id}/runs`, post({ doc }, 'PUT')),
  test: (id: string, ds: string, args: Record<string, unknown>, values: Record<string, unknown>) =>
    json<TestResult>(`/api/dash/${id}/datasets/${encodeURIComponent(ds)}/test`, post({ args, values })),
  save: (id: string, ds: string, args: Record<string, unknown>) =>
    json<{ ok: boolean; warnings: string[] }>(`/api/dash/${id}/datasets/${encodeURIComponent(ds)}`, post({ args }, 'PUT')),
  versions: (id: string, ds: string) => json<SourceVersion[]>(`/api/dash/${id}/datasets/${encodeURIComponent(ds)}/versions`),
  restore: (id: string, ds: string, version: string) =>
    json<{ ok: boolean }>(`/api/dash/${id}/datasets/${encodeURIComponent(ds)}/restore`, post({ version })),
  resume: (id: string, ds: string) => json<{ ok: boolean }>(`/api/dash/${id}/datasets/${encodeURIComponent(ds)}/resume`, post({})),
  async upload(id: string, file: File) {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`/api/dash/${id}/assets`, { method: 'POST', body: form });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(body.detail || `HTTP ${res.status}`, body.status);
    return body as { dataset: string; url: string; warnings: string[] };
  },
  assetUrl: (id: string, url: string) => `/api/dash/${id}${url}`,
};

export const api = {
  listChats: () => json<ChatSummary[]>('/api/chats'),
  createChat: () => json<ChatSummary>('/api/chats', { method: 'POST' }),
  getChat: (id: string) =>
    json<{ id: string; title: string; items: StreamItem[]; running: boolean }>(`/api/chats/${id}`),
  cancelChat: (id: string) => json<{ ok: boolean }>(`/api/chats/${id}/cancel`, { method: 'POST' }),
  feedback: (id: string, body: { type: 'positive' | 'negative'; text: string; comment?: string }) =>
    json<{ ok: boolean }>(`/api/chats/${id}/feedback`, { method: 'POST', body: JSON.stringify(body) }),
  suggestions: (chatId?: string) =>
    json<Suggestion[]>(chatId ? `/api/suggestions?chat_id=${encodeURIComponent(chatId)}` : '/api/suggestions'),
  updateChat: (id: string, patch: { title?: string; archived?: boolean }) =>
    json<{ ok: boolean }>(`/api/chats/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteChat: (id: string) => json<{ ok: boolean }>(`/api/chats/${id}`, { method: 'DELETE' }),

  listArtifacts: (project?: string | null) =>
    json<ArtifactMeta[]>(project ? `/api/artifacts?project=${encodeURIComponent(project)}` : '/api/artifacts'),
  getArtifact: (id: string) => json<ArtifactDetail>(`/api/artifacts/${id}`),
  getData: (id: string, dataset: string, params: Record<string, unknown>, cache: string) =>
    json<unknown>(`/api/artifacts/${id}/data/${encodeURIComponent(dataset)}`, {
      method: 'POST',
      body: JSON.stringify({ params, cache }),
    }),
  postRun: (id: string, record: unknown) =>
    json<{ ok: boolean }>(`/api/artifacts/${id}/runs`, { method: 'POST', body: JSON.stringify({ record }) }),
  async upload(id: string, file: File) {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`/api/artifacts/${id}/files`, { method: 'POST', body: form });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    return body;
  },
};

/** Envía un mensaje y entrega cada item del turno a medida que llega. */
export function streamChat(
  chatId: string,
  message: string,
  context: ViewContext | null,
  onItem: (item: StreamItem) => void,
  signal?: AbortSignal,
) {
  return readSse(
    fetch(`/api/chats/${chatId}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, context, client: clientTime(), ...modelChoice() }),
      signal,
    }),
    onItem,
  );
}

/** Rebobina el último turno y lo vuelve a correr (mismo mensaje del usuario). */
export function regenerateChat(chatId: string, onItem: (item: StreamItem) => void, signal?: AbortSignal) {
  return readSse(fetch(`/api/chats/${chatId}/regenerate`, { method: 'POST', signal }), onItem);
}

/** Se engancha al turno en curso de un chat: lo repite desde el principio y sigue en vivo. */
export function followChat(chatId: string, onItem: (item: StreamItem) => void, signal?: AbortSignal) {
  return readSse(fetch(`/api/chats/${chatId}/stream`, { signal }), onItem);
}

async function readSse(request: Promise<Response>, onItem: (item: StreamItem) => void): Promise<void> {
  const res = await request;
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = chunk.split('\n').find((l) => l.startsWith('data: '));
      if (line) onItem(JSON.parse(line.slice(6)) as StreamItem);
    }
  }
}
