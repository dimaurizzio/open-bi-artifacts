/** Cuenta, proyectos, chats compartidos y administración de usuarios. */

import type { StreamItem } from './types';

export type ProjectRole = 'owner' | 'editor' | 'viewer';

export interface Project {
  id: string;
  name: string;
  created_by: string;
  created_at: string;
  role: ProjectRole;
}

export interface Me {
  id: string;
  name: string;
  picture: string | null;
  admin: boolean;
  curator: boolean;
  projects: Project[];
  query_credentials: 'reader' | 'server';
  bigquery_ok: boolean;
}

export interface Member {
  user_id: string;
  role: ProjectRole;
  name: string | null;
  picture: string | null;
  last_login_at: string | null;
}

export interface ShareSummary {
  id: string;
  chat_id: string;
  owner: string;
  project_id: string;
  title: string;
  created_at: string;
}

export interface Share extends ShareSummary {
  items: StreamItem[];
}

export interface ProjectDetail extends Project {
  members: Member[];
  shares: ShareSummary[];
}

export interface AdminUser {
  id: string;
  name: string | null;
  role: 'admin' | 'member';
  status: 'active' | 'disabled';
  admin: boolean;
  invited_by: string | null;
  created_at: string;
  last_login_at: string | null;
}

export const ROLE_LABELS: Record<ProjectRole, string> = { owner: 'Owner', editor: 'Editor', viewer: 'Lector' };
export const ROLE_HELP: Record<ProjectRole, string> = {
  owner: 'Edita y administra miembros',
  editor: 'Edita dashboards y comparte chats',
  viewer: 'Solo ve',
};

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body as T;
}

const send = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });
const enc = encodeURIComponent;

export const accountApi = {
  me: () => json<Me>('/api/me'),
  logout: () => json<{ ok: boolean }>('/auth/logout', send('POST')),

  createProject: (name: string) => json<Project>('/api/projects', send('POST', { name })),
  project: (id: string) => json<ProjectDetail>(`/api/projects/${id}`),
  renameProject: (id: string, name: string) => json<{ ok: boolean }>(`/api/projects/${id}`, send('PATCH', { name })),
  archiveProject: (id: string) => json<{ ok: boolean }>(`/api/projects/${id}`, send('DELETE')),
  addMember: (id: string, email: string, role: ProjectRole) =>
    json<{ members: Member[] }>(`/api/projects/${id}/members`, send('POST', { email, role })),
  setRole: (id: string, email: string, role: ProjectRole) =>
    json<{ members: Member[] }>(`/api/projects/${id}/members/${enc(email)}`, send('PATCH', { role })),
  removeMember: (id: string, email: string) => json<{ members: Member[] }>(`/api/projects/${id}/members/${enc(email)}`, send('DELETE')),

  moveArtifact: (artifactId: string, projectId: string | null) =>
    json<{ ok: boolean }>(`/api/artifacts/${artifactId}/project`, send('POST', { project_id: projectId })),
  shareChat: (chatId: string, projectId: string) => json<ShareSummary>(`/api/chats/${chatId}/share`, send('POST', { project_id: projectId })),
  share: (id: string) => json<Share>(`/api/shares/${id}`),
  deleteShare: (id: string) => json<{ ok: boolean }>(`/api/shares/${id}`, send('DELETE')),

  users: () => json<AdminUser[]>('/api/admin/users'),
  invite: (email: string, role: 'admin' | 'member') => json<AdminUser>('/api/admin/users', send('POST', { email, role })),
  updateUser: (email: string, patch: { role?: 'admin' | 'member'; status?: 'active' | 'disabled' }) =>
    json<AdminUser>(`/api/admin/users/${enc(email)}`, send('PATCH', patch)),
};

/** Sin sesión, cualquier llamada a la API vuelve 401: se manda a la página de login. */
export function installSessionGuard() {
  const original = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const res = await original(input, init);
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (res.status === 401 && new URL(url, location.href).pathname.startsWith('/api/')) {
      location.href = `/login?next=${encodeURIComponent(location.pathname + location.hash)}`;
    }
    return res;
  };
}
