import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { ApiError, dashApi } from '@/lib/api';
import type { CalcState, DashRunsDoc, Inspection, Selection, SourceState } from '@/lib/types';

export interface DashState {
  sources: Record<string, SourceState>;
  calcs: CalcState[];
  loaders: string[];
  params: Record<string, unknown>;
  defaults: Record<string, unknown>;
}

export interface DashFrameHandle {
  /** Toolbar: todas las consultas en vivo; Fuentes: una. */
  refresh: (id?: string) => void;
  highlight: (id: string | null) => void;
  inspect: (id: string) => Promise<Inspection>;
  setParams: (params: Record<string, unknown>) => void;
}

interface Props {
  dashboardId: string;
  frameKey: string;
  theme: 'light' | 'dark';
  mode: 'page' | 'data';
  link?: string;
  onState: (s: DashState) => void;
  onRuns: (doc: DashRunsDoc) => void;
  onSelect: (sel: Selection) => void;
  onSelection: (sel: { ids: string[]; at?: string }) => void;
  onLink: (name: string) => void;
}

/**
 * Iframe sandbox (allow-scripts, sin allow-same-origin) y CSP sin red: la página del
 * dashboard no alcanza nada por su cuenta. Este componente es su único puente: cada
 * llamada de una fuente pasa por acá hacia el servidor, que aplica la lista blanca.
 */
export const DashFrame = forwardRef<DashFrameHandle, Props>(function DashFrame(
  { dashboardId, frameKey, theme, mode, link, onState, onRuns, onSelect, onSelection, onLink },
  ref,
) {
  const frame = useRef<HTMLIFrameElement>(null);
  const cb = useRef({ onState, onRuns, onSelect, onSelection, onLink, theme, mode });
  cb.current = { onState, onRuns, onSelect, onSelection, onLink, theme, mode };
  const inspections = useRef(new Map<number, (d: Inspection) => void>());
  const seq = useRef(0);
  const runsTimer = useRef<number | undefined>(undefined);

  const post = (msg: unknown) => frame.current?.contentWindow?.postMessage(msg, '*');

  useImperativeHandle(ref, () => ({
    refresh: (id) => post({ type: 'dash:refresh', id }),
    highlight: (id) => post({ type: 'dash:highlight', id }),
    setParams: (params) => post({ type: 'dash:params', params }),
    inspect: (id) =>
      new Promise<Inspection>((resolve) => {
        const reqId = ++seq.current;
        inspections.current.set(reqId, resolve);
        post({ type: 'dash:inspect', reqId, id });
        window.setTimeout(() => {
          if (inspections.current.delete(reqId)) resolve({ id, kind: 'missing', status: 'missing' });
        }, 4000);
      }),
  }));

  useEffect(() => {
    const reply = (win: Window, reqId: number, ok: boolean, payload?: unknown, err?: unknown) =>
      win.postMessage(
        ok
          ? { type: 'dash:reply', reqId, ok, payload }
          : { type: 'dash:reply', reqId, ok, error: (err as Error).message, status: (err as ApiError).status ?? 'failed' },
        '*',
      );
    const onMessage = async (ev: MessageEvent) => {
      const win = frame.current?.contentWindow;
      if (!win || ev.source !== win) return;
      const m = (ev.data || {}) as Record<string, any>;
      switch (m.type) {
        case 'dash:load':
          try {
            reply(win, m.reqId, true, await dashApi.load(dashboardId, String(m.id), { values: m.values || {}, cache: m.cache || 'prefer', raw: !!m.raw }));
          } catch (e) {
            reply(win, m.reqId, false, undefined, e);
          }
          break;
        case 'dash:call':
          try {
            reply(win, m.reqId, true, await dashApi.call(dashboardId, String(m.id), String(m.tool), m.args || {}));
          } catch (e) {
            reply(win, m.reqId, false, undefined, e);
          }
          break;
        case 'dash:runs':
          cb.current.onRuns(m.doc as DashRunsDoc);
          window.clearTimeout(runsTimer.current);
          runsTimer.current = window.setTimeout(() => void dashApi.putRuns(dashboardId, m.doc).catch(() => undefined), 1200);
          break;
        case 'dash:state':
          cb.current.onState({ sources: m.sources || {}, calcs: m.calcs || [], loaders: m.loaders || [], params: m.params || {}, defaults: m.defaults || {} });
          break;
        case 'dash:inspected': {
          const done = inspections.current.get(m.reqId);
          inspections.current.delete(m.reqId);
          done?.(m.detail as Inspection);
          break;
        }
        case 'dash:select':
          cb.current.onSelect(m as unknown as Selection);
          break;
        case 'dash:selection':
          cb.current.onSelection({ ids: m.ids || [], at: m.at });
          break;
        case 'dash:link':
          cb.current.onLink(String(m.name || ''));
          break;
        case 'dash:ready':
          win.postMessage({ type: 'dash:theme', theme: cb.current.theme }, '*');
          win.postMessage({ type: 'dash:mode', mode: cb.current.mode }, '*');
          break;
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [dashboardId]);

  useEffect(() => post({ type: 'dash:theme', theme }), [theme]);
  useEffect(() => post({ type: 'dash:mode', mode }), [mode]);

  // La url se arma al montar cada versión: un link nuevo (dash.setLink) no recarga la página.
  const src = useRef<{ key: string; url: string } | null>(null);
  if (!src.current || src.current.key !== frameKey) {
    const hash = link ? `#${encodeURIComponent(link)}` : '';
    src.current = { key: frameKey, url: `/viewer/${dashboardId}?theme=${theme}&k=${encodeURIComponent(frameKey)}${hash}` };
  }

  return (
    <iframe
      key={frameKey}
      ref={frame}
      title="Dashboard"
      className="block size-full border-0 bg-background"
      sandbox="allow-scripts"
      src={src.current.url}
    />
  );
});
