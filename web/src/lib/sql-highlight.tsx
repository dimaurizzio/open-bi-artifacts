import type { ReactNode } from 'react';

const KEYWORDS = new Set(
  (
    'select from where and or not as on join left right inner outer full cross group by order having limit ' +
    'with union all distinct case when then else end in is null between like asc desc over partition window ' +
    'qualify unnest struct array interval date timestamp true false cast safe_cast exists using'
  ).split(' '),
);

const TOKEN = /(--[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/)|('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")|(`[^`]*`)|(\{\{\s*\w+\s*\}\})|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_][A-Za-z0-9_]*\b)(?=\s*\()|(\b[A-Za-z_]+\b)/g;

/** Resaltado de SQL sin dependencias: comentarios, strings, identificadores, parámetros, funciones. */
export function highlightSql(sql: string, renderParam?: (name: string, key: number) => ReactNode): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of sql.matchAll(TOKEN)) {
    const start = m.index ?? 0;
    if (start > last) out.push(sql.slice(last, start));
    const [text, comment, str, ident, param, num, fn, word] = m;
    if (param && renderParam) {
      out.push(renderParam(param.replace(/[{}\s]/g, ''), i++));
      last = start + text.length;
      continue;
    }
    let cls = '';
    if (comment) cls = 'text-muted-foreground italic';
    else if (str) cls = 'text-emerald-700 dark:text-emerald-400';
    else if (ident) cls = 'text-amber-700 dark:text-amber-300';
    else if (param) cls = 'rounded bg-primary/10 px-0.5 text-primary font-semibold';
    else if (num) cls = 'text-orange-600 dark:text-orange-300';
    else if (fn) cls = 'text-sky-700 dark:text-sky-300';
    else if (word && KEYWORDS.has(word.toLowerCase())) cls = 'text-violet-700 dark:text-violet-300 font-medium';
    out.push(cls ? <span key={i++} className={cls}>{text}</span> : text);
    last = start + text.length;
  }
  if (last < sql.length) out.push(sql.slice(last));
  return out;
}
