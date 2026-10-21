import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { api } from '@/lib/api';
import type { CalcInfo, RunRecord } from '@/lib/types';

export interface SourceStatus {
  loading?: boolean;
  cached_at?: string;
  stale?: boolean;
  rows?: number;
  truncated?: boolean;
  mode?: string;
  params_used?: Record<string, unknown> | null;
  error?: string;
}

export interface ViewerHandle {
  /** Vuelve a correr las fuentes en vivo; con `dataset`, solo esa. */
  reload: (dataset?: string) => void;
  highlight: (dataset: string | null) => void;
}

interface Props {
  artifactId: string;
  frameKey: string; // cambia cuando hay que recargar la página del visor
  theme: 'light' | 'dark';
  mode: 'page' | 'sources';
  onRun: (record: RunRecord) => void;
  onCalcs: (calcs: CalcInfo[]) => void;
  onStatus: (statuses: Record<string, SourceStatus>) => void;
  onSelect: (dataset: string) => void;
}

/**
 * Iframe sandbox (allow-scripts, sin allow-same-origin): origen opaco, sin acceso a
 * cookies ni storage del shell, y con CSP sin red. Este componente es el único
 * puente de datos: recibe dash:fetch, consulta al backend y responde dash:data.
 */
export const ViewerFrame = forwardRef<ViewerHandle, Props>(function ViewerFrame(
  { artifactId, frameKey, theme, mode, onRun, onCalcs, onStatus, onSelect },
  ref,
) {
  const frame = useRef<HTMLIFrameElement>(null);
  const cb = useRef({ onRun, onCalcs, onStatus, onSelect, theme, mode });
  cb.current = { onRun, onCalcs, onStatus, onSelect, theme, mode };

  const post = (msg: unknown) => frame.current?.contentWindow?.postMessage(msg, '*');

  useImperativeHandle(ref, () => ({
    reload: (dataset) => post({ type: 'dash:reload', dataset }),
    highlight: (dataset) => post({ type: 'dash:highlight', dataset }),
  }));

  useEffect(() => {
    const onMessage = async (ev: MessageEvent) => {
      const win = frame.current?.contentWindow;
      if (!win || ev.source !== win) return;
      const m = (ev.data || {}) as Record<string, any>;
      if (m.type === 'dash:fetch') {
        try {
          const payload = await api.getData(artifactId, m.dataset, m.params || {}, m.cache || 'prefer');
          win.postMessage({ type: 'dash:data', reqId: m.reqId, ok: true, payload }, '*');
        } catch (e) {
          win.postMessage({ type: 'dash:data', reqId: m.reqId, ok: false, error: (e as Error).message }, '*');
        }
      } else if (m.type === 'dash:run') {
        cb.current.onRun(m.record as RunRecord);
      } else if (m.type === 'dash:calcs') {
        cb.current.onCalcs((m.calcs || []) as CalcInfo[]);
      } else if (m.type === 'dash:status') {
        cb.current.onStatus({ ...(m.statuses || {}) });
      } else if (m.type === 'dash:select' && typeof m.dataset === 'string') {
        cb.current.onSelect(m.dataset);
      } else if (m.type === 'dash:ready') {
        win.postMessage({ type: 'dash:theme', theme: cb.current.theme }, '*');
        win.postMessage({ type: 'dash:mode', mode: cb.current.mode }, '*');
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [artifactId]);

  useEffect(() => {
    post({ type: 'dash:theme', theme });
  }, [theme]);

  useEffect(() => {
    post({ type: 'dash:mode', mode });
  }, [mode]);

  return (
    <iframe
      key={frameKey}
      ref={frame}
      title="Vista del artifact"
      className="block size-full border-0 bg-background"
      sandbox="allow-scripts"
      src={`/viewer/${artifactId}?theme=${cb.current.theme}&k=${encodeURIComponent(frameKey)}`}
    />
  );
});
