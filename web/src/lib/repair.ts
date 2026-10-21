/**
 * Reparación automática: cuando el agente termina de escribir un dashboard y la página,
 * al dibujarse, reporta problemas (fuentes que fallan, números que no coinciden con sus
 * datos, errores del código), se le avisa solo para que los corrija.
 *
 * Solo después de un turno del agente que tocó ese dashboard, y como mucho
 * MAX_ATTEMPTS veces seguidas: si no lo resuelve, quedan a la vista en "N problemas".
 */
import type { DashRunsDoc } from './types';

export const MAX_ATTEMPTS = 2;
// La página manda varias versiones de las corridas mientras carga: se espera a que se calme.
export const SETTLE_MS = 2000;

export interface PageProblems {
  failed: { id: string; message?: string }[];
  flagged: { source: string; field?: string }[];
  flaggedCount: number;
  errors: { name: string; message: string }[];
  total: number;
}

export function problemsOf(doc: DashRunsDoc | null): PageProblems {
  const failed = Object.entries(doc?.datasets ?? {})
    .filter(([, d]) => d.status !== 'ok' && d.status !== 'loading')
    .map(([id, d]) => ({ id, message: d.message }));
  const flagged = (doc?.marks ?? []).map((m) => ({ source: m.source, field: m.field }));
  const flaggedCount = doc?.flagged ?? flagged.length;
  const errors = (doc?.errors ?? []).map((e) => ({ name: e.name, message: e.message }));
  return { failed, flagged, flaggedCount, errors, total: failed.length + flaggedCount + errors.length };
}

/** Todas las fuentes terminaron de cargar (ok o con error): recién ahí las corridas son definitivas. */
export function settled(doc: DashRunsDoc | null): boolean {
  const ds = Object.values(doc?.datasets ?? {});
  return ds.length > 0 && ds.every((d) => d.status !== 'loading');
}

/** El mensaje que recibe el agente: qué falló, sin valores de los datos. */
export function repairMessage(p: PageProblems, attempt: number): string {
  const parts: string[] = [];
  if (p.failed.length) {
    parts.push(`${p.failed.length === 1 ? 'una fuente no cargó' : `${p.failed.length} fuentes no cargaron`} (${p.failed.map((f) => (f.message ? `${f.id}: ${f.message.slice(0, 160)}` : f.id)).join('; ')})`);
  }
  if (p.flaggedCount) {
    const which = [...new Set(p.flagged.map((m) => (m.field ? `${m.source}.${m.field}` : m.source)))].slice(0, 6).join(', ');
    parts.push(`${p.flaggedCount === 1 ? 'un número marcado no coincide' : `${p.flaggedCount} números marcados no coinciden`} con sus datos${which ? ` (${which})` : ''}`);
  }
  if (p.errors.length) {
    parts.push(`${p.errors.length === 1 ? 'un error' : `${p.errors.length} errores`} del código (${p.errors.slice(0, 3).map((e) => `${e.name}: ${e.message.slice(0, 160)}`).join('; ')})`);
  }
  const retry = attempt > 1 ? ' Es el segundo intento: si no podés resolverlo, explicá qué falta.' : '';
  return (
    `Revisión automática: al abrirse, la página que armaste reportó ${parts.join('; ')}. ` +
    `Leé el documento de corridas (read_db, colección data/users/me, doc runs), corregí la causa y volvé a correr las fuentes que cambies.${retry}`
  );
}
