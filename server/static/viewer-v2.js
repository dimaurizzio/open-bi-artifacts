/* Visor de dashboards, contrato 2.
 *
 * Corre dentro de un iframe sandbox sin red. Monta files/index.html en #dash-root,
 * corre cada script en su propio scope con `dash` y `d3`, carga los datasets a pedido
 * (el shell padre media cada llamada), estampa estados en las marcas, verifica los
 * números marcados y escribe el documento de corridas del lector.
 */
(function () {
  'use strict';
  const CFG = window.__DASH__;
  const NONCE = document.currentScript && document.currentScript.nonce;
  const PARENT = window.parent;
  const root = document.getElementById('dash-root');
  const stoppedEl = document.getElementById('dash-stopped');
  const DATASETS = CFG.datasets || {};
  const FILES = CFG.files || {};
  const DEFAULTS = CFG.params || {};
  const LOADER_TIMEOUT = 180000;

  // ---------------------------------------------------------------- estado
  let params = clone(DEFAULTS);
  const sources = {};     // id -> {status, data, columns, meta, message, refreshing, at, stale, rows, cut, ms, kind, args, failure}
  const calcs = {};       // id -> {inputs, fn, title, description, key, result, error}
  const loaders = {};     // id -> {description, fn}
  const handlers = [];
  const errors = new Map(); // kind|name|message -> {kind, name, message, file, count}
  let marks = [], flagged = 0;
  let booted = false;
  let mode = 'page';
  let currentFile = null;

  function clone(v) { return JSON.parse(JSON.stringify(v == null ? {} : v)); }
  function post(type, payload) { PARENT.postMessage(Object.assign({ type }, payload || {}), '*'); }

  // ---------------------------------------------------------------- puente con el shell
  let seq = 0;
  const pending = new Map();
  function ask(type, payload, timeout) {
    return new Promise((resolve, reject) => {
      const reqId = ++seq;
      pending.set(reqId, { resolve, reject });
      post(type, Object.assign({ reqId }, payload));
      setTimeout(() => {
        if (pending.has(reqId)) { pending.delete(reqId); reject(Object.assign(new Error('Se agotó el tiempo de espera.'), { status: 'failed' })); }
      }, timeout || 330000);
    });
  }

  window.addEventListener('message', (ev) => {
    if (ev.source !== PARENT) return;
    const m = ev.data || {};
    if (m.type === 'dash:reply' && pending.has(m.reqId)) {
      const p = pending.get(m.reqId);
      pending.delete(m.reqId);
      if (m.ok) p.resolve(m.payload);
      else p.reject(Object.assign(new Error(m.error || 'La fuente no cargó.'), { status: m.status || 'failed' }));
    } else if (m.type === 'dash:theme') {
      document.documentElement.setAttribute('data-theme', m.theme === 'dark' ? 'dark' : 'light');
    } else if (m.type === 'dash:mode') {
      mode = m.mode === 'data' ? 'data' : 'page';
      document.body.classList.toggle('dash-data-mode', mode === 'data');
    } else if (m.type === 'dash:highlight') {
      root.querySelectorAll('.dash-hl').forEach((el) => el.classList.remove('dash-hl'));
      if (m.id) marksOf(m.id).forEach((el) => el.classList.add('dash-hl'));
    } else if (m.type === 'dash:refresh') {
      // Toolbar: solo consultas en vivo; Fuentes: una.
      const ids = m.id ? [m.id] : Object.keys(sources).filter((id) => DATASETS[id] && DATASETS[id].kind === 'query');
      ids.forEach((id) => load(id, 'refresh'));
    } else if (m.type === 'dash:inspect') {
      post('dash:inspected', { reqId: m.reqId, detail: inspect(m.id) });
    } else if (m.type === 'dash:params') {
      if (m.reset) resetParams(); else setParams(m.params || {});
    }
  });

  // ---------------------------------------------------------------- errores de la página
  function noteError(kind, e, file) {
    const name = (e && e.name) || 'Error';
    const message = String((e && e.message) || e || 'Error').slice(0, 400);
    const key = kind + '|' + name + '|' + message;
    const prev = errors.get(key);
    if (prev) prev.count += 1;
    else errors.set(key, { kind, name, message, file: file || undefined, count: 1 });
    if (kind === 'stopped') stoppedEl.hidden = false;
    scheduleReport();
  }
  window.__dashStopped = (e, file) => noteError('stopped', e, file);
  window.addEventListener('error', (e) => {
    if (currentFile) noteError('stopped', e.error || { name: 'SyntaxError', message: e.message }, currentFile);
    else noteError('error', e.error || { message: e.message });
  });
  window.addEventListener('unhandledrejection', (e) => noteError('rejection', e.reason));

  // ---------------------------------------------------------------- parámetros
  function cleanValue(v) {
    if (v === null || v === undefined) return null;
    if (typeof v === 'boolean') return v;
    if (typeof v === 'number') return isFinite(v) ? v : undefined;
    if (typeof v === 'string') return /[\r\n]/.test(v) ? undefined : v;
    if (Array.isArray(v)) {
      const out = v.map(cleanValue);
      return out.some((x) => x === undefined || Array.isArray(x)) ? undefined : out;
    }
    return undefined;
  }
  function applyParams(next) {
    const changed = Object.keys(Object.assign({}, params, next)).filter((k) => JSON.stringify(params[k]) !== JSON.stringify(next[k]));
    params = next;
    if (!changed.length) return;
    const affected = Object.keys(sources).filter((id) => DATASETS[id] && (DATASETS[id].params || []).some((p) => changed.includes(p)));
    affected.forEach((id) => load(id, 'auto'));
    postState();
    schedule();
  }
  function setParams(obj) {
    const next = Object.assign({}, params);
    Object.entries(obj || {}).forEach(([k, v]) => {
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(k)) return;
      const c = cleanValue(v);
      if (c !== undefined) next[k] = c;
    });
    applyParams(next);
  }
  function resetParams() { applyParams(clone(DEFAULTS)); }
  function valuesFor(id) {
    const out = {};
    ((DATASETS[id] && DATASETS[id].params) || []).forEach((p) => { out[p] = p in params ? params[p] : null; });
    return out;
  }

  // ---------------------------------------------------------------- parsers (read.*)
  function typeCell(v, f) {
    if (v === null || v === undefined) return null;
    if (f.mode === 'REPEATED') return (v || []).map((x) => typeCell(x && typeof x === 'object' && 'v' in x ? x.v : x, Object.assign({}, f, { mode: 'NULLABLE' })));
    const t = (f.type || 'STRING').toUpperCase();
    if (t === 'RECORD' || t === 'STRUCT') {
      const cells = (v && v.f) || [];
      const o = {};
      (f.fields || []).forEach((sf, i) => { o[sf.name] = typeCell(cells[i] && cells[i].v, sf); });
      return o;
    }
    if (typeof v === 'object') return v;
    if (t === 'INTEGER' || t === 'INT64') { const n = Number(v); return Math.abs(n) >= 2 ** 53 ? String(v) : n; }
    if (t === 'FLOAT' || t === 'FLOAT64' || t === 'NUMERIC' || t === 'BIGNUMERIC') return Number(v);
    if (t === 'BOOLEAN' || t === 'BOOL') return String(v).toLowerCase() === 'true';
    if (t === 'TIMESTAMP' && /^[\d.eE+-]+$/.test(String(v))) {
      const s = String(v);
      const secs = /[.eE]/.test(s) ? Number(s) : s.length > 12 ? Number(s) / 1e6 : Number(s);
      return new Date(secs * 1000).toISOString();
    }
    return v;
  }
  function makeRead(call) {
    return {
      async bigquery(answer) {
        let a = typeof answer === 'string' ? JSON.parse(answer) : answer;
        if (a && a.error) throw new Error(a.error.message || String(a.error));
        const job = (a && a.jobReference) || {};
        let tries = 0;
        while (a && a.jobComplete === false) {
          if (!call || ++tries > 120) throw new Error('La consulta sigue corriendo.');
          await new Promise((r) => setTimeout(r, 1500));
          a = await call('get_query_results', { jobId: job.jobId, location: job.location });
        }
        const fields = ((a && a.schema) || {}).fields || [];
        const data = ((a && a.rows) || []).map((r) => {
          const o = {};
          fields.forEach((f, i) => { o[f.name] = typeCell(r.f && r.f[i] ? r.f[i].v : null, f); });
          return o;
        });
        const meta = [];
        if (job.jobId) meta.push({ label: 'Job', value: String(job.jobId), type: 'text' });
        if (a && a.totalBytesProcessed) meta.push({ label: 'Datos escaneados', value: bytes(Number(a.totalBytesProcessed)), type: 'text' });
        return { data, columns: fields.map((f) => f.name), cut: !!(a && a.pageToken), meta };
      },
      governed() { throw new Error('read.governed: no hay un conector con respuestas <query_result> en esta plataforma.'); },
      snowflake() { throw new Error('read.snowflake: no hay un conector de Snowflake en esta plataforma.'); },
      databricks() { throw new Error('read.databricks: no hay un conector de Databricks en esta plataforma.'); },
    };
  }
  function bytes(n) {
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : n.toFixed(0)) + ' ' + u[i];
  }

  /** Lo que devuelve un loader (o la respuesta sin loader) como {data, columns, cut, meta}. */
  function normalize(out, fallbackMeta) {
    let data = out, columns, cut = false, meta = fallbackMeta || [];
    if (out && !Array.isArray(out) && typeof out === 'object') {
      if (Array.isArray(out.rows) && ('columns' in out || 'cut' in out || 'meta' in out)) { data = out.rows; columns = out.columns; cut = !!out.cut; if (out.meta) meta = out.meta; }
      else if ('data' in out && ('columns' in out || 'meta' in out)) { data = out.data; columns = out.columns; cut = !!out.cut; if (out.meta) meta = out.meta; }
      else { const k = ['rows', 'data', 'results'].find((x) => Array.isArray(out[x]) && out[x].every(isRow)); if (k) data = out[k]; }
    }
    if (Array.isArray(data) && data.every(isRow) && !columns) {
      const seen = [];
      data.forEach((r) => Object.keys(r).forEach((c) => { if (!seen.includes(c)) seen.push(c); }));
      columns = seen;
    }
    return { data, columns: columns ? columns.map(String) : undefined, cut, meta: Array.isArray(meta) ? meta : [] };
  }
  function isRow(r) { return r && typeof r === 'object' && !Array.isArray(r); }

  // ---------------------------------------------------------------- carga de datasets
  function entry(id) {
    if (!sources[id]) sources[id] = { status: 'idle', data: undefined, columns: undefined, meta: [], message: undefined, refreshing: false, kind: DATASETS[id] && DATASETS[id].kind };
    return sources[id];
  }
  function want(id) {
    if (DATASETS[id]) {
      const s = entry(id);
      if (s.status === 'idle') { s.status = 'loading'; if (booted) load(id, 'prefer'); }
    } else if (calcs[id]) {
      calcs[id].inputs.forEach(want);
    }
  }

  const generation = {};
  async function load(id, cache) {
    if (!DATASETS[id]) return;
    const s = entry(id);
    const gen = (generation[id] = (generation[id] || 0) + 1);
    const hadData = s.data !== undefined && s.status === 'ok';
    if (hadData) s.refreshing = true; else s.status = 'loading';
    schedule();
    postState();
    const started = performance.now();
    const loader = loaders[id];
    const ds = DATASETS[id];
    try {
      const payload = await ask('dash:load', { id, values: valuesFor(id), cache, raw: !!(loader && ds.kind === 'query') });
      if (gen !== generation[id]) return;
      let result;
      if (loader && ds.kind === 'query') {
        const call = (tool, args) => ask('dash:call', { id, tool, args: args || {} }, LOADER_TIMEOUT);
        const run = Promise.resolve(loader.fn({ answer: payload.answer, call, args: payload.args, read: makeRead(call) }));
        const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error('El loader no terminó en tres minutos.')), LOADER_TIMEOUT));
        try {
          result = normalize(await Promise.race([run, timeout]), payload.meta);
        } catch (e) {
          const keys = payload.answer && typeof payload.answer === 'object' ? Object.keys(payload.answer).slice(0, 8).join(', ') : typeof payload.answer;
          throw Object.assign(new Error(`${(e && e.message) || e} (la respuesta trae: ${keys})`), { status: 'failed' });
        }
      } else {
        result = normalize({ data: payload.data, columns: payload.columns, cut: payload.cut, meta: payload.meta || [] });
      }
      Object.assign(s, {
        status: 'ok', data: result.data, columns: result.columns, meta: result.meta, cut: result.cut || !!payload.cut,
        message: payload.notice || undefined, refreshing: false, at: payload.at, stale: !!payload.stale, kind: ds.kind,
        rows: Array.isArray(result.data) ? result.data.length : undefined, ms: Math.round(performance.now() - started),
        args: payload.args, failure: undefined, paused: !!ds.paused,
      });
      // Caché viejo: se muestra ya y se refresca detrás.
      if (payload.stale && ds.kind === 'query' && cache === 'prefer') setTimeout(() => load(id, 'refresh'), 50);
    } catch (e) {
      if (gen !== generation[id]) return;
      Object.assign(s, { status: 'error', message: (e && e.message) || String(e), failure: (e && e.status) || 'failed', refreshing: false, data: undefined, at: new Date().toISOString(), ms: Math.round(performance.now() - started) });
    }
    schedule();
    postState();
    scheduleReport();
  }

  // ---------------------------------------------------------------- cálculos
  function calcView(id) {
    const c = calcs[id];
    const inputs = c.inputs.map((i) => view(i));
    if (inputs.some((v) => v.status === 'error' || v.status === 'missing' || v.status === 'connect' || v.status === 'declined')) {
      return { status: 'error', message: 'Una de sus fuentes no cargó.', data: undefined, refreshing: false, meta: [] };
    }
    if (inputs.some((v) => v.status !== 'ok')) return { status: 'loading', data: undefined, refreshing: false, meta: [] };
    const key = JSON.stringify(c.inputs.map((i) => (sources[i] ? [sources[i].at, generation[i]] : calcs[i] ? calcs[i].key : null)));
    if (c.key !== key) {
      c.key = key;
      try {
        c.result = normalize(c.fn.apply(null, inputs.map((v) => v.data)));
        c.error = undefined;
      } catch (e) {
        c.error = (e && e.message) || String(e);
        c.result = undefined;
        noteError('stopped', e, 'dash.calc ' + id);
      }
    }
    if (c.error) return { status: 'error', message: c.error, data: undefined, refreshing: false, meta: [] };
    const refreshing = inputs.some((v) => v.refreshing);
    return { status: 'ok', data: c.result.data, columns: c.result.columns, refreshing, meta: [] };
  }

  /** Lo que devuelve dash.data(id). */
  function view(id) {
    let v;
    if (calcs[id]) { want(id); v = calcView(id); }
    else if (DATASETS[id]) {
      want(id);
      const s = entry(id);
      v = { status: s.status === 'idle' ? 'loading' : s.status, data: s.status === 'ok' ? s.data : Array.isArray(s.data) ? s.data : undefined, columns: s.columns, message: s.message, refreshing: !!s.refreshing, meta: s.meta || [] };
      if (v.status !== 'ok' && v.data === undefined) v.data = [];
    } else {
      v = { status: 'missing', data: [], message: `No existe la fuente ${id}.`, refreshing: false, meta: [] };
    }
    // Compatibilidad con páginas viejas: .rows, .columns y .value.
    v.rows = Array.isArray(v.data) ? v.data : [];
    if (!v.columns && v.rows.length && isRow(v.rows[0])) v.columns = Object.keys(v.rows[0]);
    v.value = v.rows.length && isRow(v.rows[0]) ? v.rows[0][(v.columns || [])[0]] : v.data;
    return v;
  }

  // ---------------------------------------------------------------- dibujo
  let drawQueued = false;
  function schedule() {
    if (drawQueued || !booted) return;
    drawQueued = true;
    requestAnimationFrame(draw);
  }
  function runHandler(fn) {
    try { fn(); } catch (e) { noteError('stopped', e, 'dash.onData'); }
  }
  function draw() {
    drawQueued = false;
    handlers.forEach(runHandler);
    stamp();
    scheduleVerify();
  }

  // Redibujar cuando cambia el ancho (panel, ventana): onData vuelve a correr.
  let lastWidth = 0, resizeTimer = null;
  new ResizeObserver(() => {
    const w = Math.round(root.clientWidth);
    if (Math.abs(w - lastWidth) < 1) return;
    lastWidth = w;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(schedule, 60);
  }).observe(root);

  // ---------------------------------------------------------------- marcas y estados
  function marksOf(id) {
    return [...root.querySelectorAll('[data-source]')].filter((el) => el.getAttribute('data-source').split(/\s+/).includes(id));
  }
  const PLACEHOLDER = /^\s*[—–-]?\s*$/;
  function stamp() {
    let loading = false, failed = false;
    root.querySelectorAll('[data-source]').forEach((el) => {
      const ids = el.getAttribute('data-source').split(/\s+/).filter(Boolean);
      const vs = ids.map((id) => view(id));
      let state = null;
      if (vs.some((v) => v.status === 'error' || v.status === 'missing' || v.status === 'connect' || v.status === 'declined')) { state = 'error'; failed = true; }
      else if (vs.some((v) => v.status === 'loading')) { state = 'loading'; loading = true; }
      else if (vs.some((v) => v.refreshing)) state = 'refreshing';
      else if (el.hasAttribute('data-field') && !el.children.length && PLACEHOLDER.test(el.textContent || '')) state = 'empty';
      if (state) el.setAttribute('data-dash-source-state', state);
      else el.removeAttribute('data-dash-source-state');
    });
    if (loading) root.setAttribute('data-dash-state', 'loading');
    else if (failed) root.setAttribute('data-dash-state', 'error');
    else root.removeAttribute('data-dash-state');
  }

  // ---------------------------------------------------------------- verificación de números
  function parseWhere(s) {
    const out = {};
    String(s).split('&').forEach((pair) => {
      if (!pair) return;
      const i = pair.indexOf('=');
      const k = decodeURIComponent(i < 0 ? pair : pair.slice(0, i));
      out[k] = decodeURIComponent(i < 0 ? '' : pair.slice(i + 1));
    });
    return out;
  }
  function rowsWhere(rows, where) {
    const w = parseWhere(where);
    return rows.filter((r) => Object.entries(w).every(([k, v]) => String(r[k] === null || r[k] === undefined ? '' : r[k]) === v));
  }
  function numberCandidates(text) {
    const s = String(text).replace(/[  ]/g, ' ').replace('−', '-');
    const m = s.match(/-?\d[\d.,]*/);
    if (!m) return [];
    const raw = m[0];
    const dec = (str, sep) => (str.includes(sep) ? str.split(sep).pop().length : 0);
    const rest = s.slice(m.index + raw.length).trim();
    let mult = 1;
    if (/^(k\b|mil\b)/i.test(rest)) mult = 1e3;
    else if (/^(m\b|mm\b|mill)/i.test(rest)) mult = 1e6;
    else if (/^(b\b|bn\b|g\b)/i.test(rest)) mult = 1e9;
    else if (/^t\b/i.test(rest)) mult = 1e12;
    // % y puntos porcentuales ("+0,4 pp", "p.p.", "puntos") se comparan contra una fracción.
    const pct = /%/.test(rest.slice(0, 2)) || /^%/.test(rest) || /^(pp\b|p\.\s?p\.|puntos?\b)/i.test(rest);
    const opts = [
      [Number(raw.replace(/,/g, '')), dec(raw, '.')],
      [Number(raw.replace(/\./g, '').replace(',', '.')), dec(raw, ',')],
    ];
    return opts.filter(([n]) => isFinite(n)).map(([n, d]) => ({ n: n * mult, tol: 0.5 * Math.pow(10, -d) * mult, pct }));
  }
  const DATE_FORMATS = [
    '%b %-d', '%b %-d, %Y', '%B %-d', '%B %-d, %Y', '%Y-%m-%d', '%-d %b', '%b %Y', '%B %Y', '%Y', '%-m/%-d',
    '%d/%m/%Y', '%-d/%-m', '%d/%m', '%-d/%m', '%d/%m/%y', '%-d/%-m/%Y', '%-d/%-m/%y',
    '%-d %b %Y', '%-d de %B', '%-d de %B de %Y', '%B de %Y',
  ];
  // Las páginas se escriben en castellano: las fechas se reconocen también con meses en español.
  const ES = d3.timeFormatLocale({
    dateTime: '%A, %e de %B de %Y, %X', date: '%d/%m/%Y', time: '%H:%M:%S', periods: ['AM', 'PM'],
    days: ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'],
    shortDays: ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'],
    months: ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'],
    shortMonths: ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'],
  });
  const DATE_FORMATTERS = DATE_FORMATS.flatMap((f) => [d3.utcFormat(f), d3.timeFormat(f), ES.utcFormat(f)]);
  const plain = (s) => s.toLowerCase().replace(/\./g, '').replace(/\bsept\b/g, 'sep');
  function sameValue(shown, value) {
    const t = String(shown || '').trim();
    if (value === null || value === undefined || value === '') return PLACEHOLDER.test(t) || /^(n\/a|null|none)$/i.test(t);
    if (typeof value === 'boolean') return /^(true|false|yes|no|sí|si|✓|✗)$/i.test(t);
    const num = typeof value === 'number' ? value : (typeof value === 'string' && /^-?\d+(\.\d+)?([eE][-+]?\d+)?$/.test(value.trim()) ? Number(value) : null);
    if (num !== null) {
      const cands = numberCandidates(t);
      if (!cands.length) return t.includes(String(value));
      return cands.some(({ n, tol, pct }) => {
        const tries = pct ? [[n / 100, tol / 100], [n, tol]] : [[n, tol]];
        return tries.some(([x, e]) => Math.abs(x - num) <= e + 1e-9 || Math.abs(Math.abs(x) - Math.abs(num)) <= e + 1e-9);
      });
    }
    const sv = String(value);
    if (t === sv || t.includes(sv) || (t.length > 3 && sv.includes(t.replace(/…$/, '')))) return true;
    if (t.toLowerCase() === sv.toLowerCase()) return true;
    if (/^\d{4}-\d{2}(-\d{2})?/.test(sv)) {
      const d = new Date(sv.length === 7 ? sv + '-01T00:00:00Z' : sv.length === 10 ? sv + 'T00:00:00Z' : sv);
      if (!isNaN(d)) {
        const shownPlain = plain(t);
        return DATE_FORMATTERS.some((fmt) => shownPlain.includes(plain(fmt(d))));
      }
    }
    return false;
  }
  function describeMark(el) {
    if (el.id) return '#' + el.id;
    const cls = [...el.classList].filter((c) => !c.startsWith('dash-')).slice(0, 2).join('.');
    const host = el.closest('[id]');
    return (host && host !== el ? '#' + host.id + ' ' : '') + el.tagName.toLowerCase() + (cls ? '.' + cls : '');
  }
  function cellMark(src, field, where, row, shown, el) {
    const v = view(src);
    if (v.status !== 'ok') return null;
    const rows = Array.isArray(v.data) ? v.data : [];
    if (!field) {
      if (!Array.isArray(v.data) && (typeof v.data === 'number' || typeof v.data === 'string') && !sameValue(shown, v.data)) {
        return { mark: describeMark(el), source: src, shown, inData: v.data };
      }
      return null;
    }
    let cands;
    if (row !== null) cands = rows[Number(row)] ? [rows[Number(row)]] : [];
    else if (where !== null) cands = rowsWhere(rows, where);
    else if (rows.length === 1) cands = rows;
    else if (isRow(v.data)) cands = [v.data];
    else return null;
    const base = { mark: describeMark(el), source: src, field };
    if (where !== null) base.where = where;
    if (row !== null) base.row = Number(row);
    if (!cands.length) return Object.assign(base, { shown, reason: 'no row matches' });
    if (!(field in cands[0])) return Object.assign(base, { shown, reason: `no column ${field}` });
    if (cands.some((r) => sameValue(shown, r[field]))) return null;
    return Object.assign(base, { shown, inData: cands[0][field] });
  }
  function tableKeyOf(tr, keyCols, ths) {
    if (tr.hasAttribute('data-key')) {
      const raw = tr.getAttribute('data-key');
      return keyCols.length > 1 ? raw.split(',').map(decodeURIComponent) : [raw];
    }
    return keyCols.map((k) => {
      const i = ths.findIndex((th) => th && th.getAttribute('data-field') === k);
      const cell = i >= 0 ? tr.cells[i] : null;
      return cell ? cell.textContent.trim() : null;
    });
  }
  function verify() {
    const found = [];
    root.querySelectorAll('[data-source]').forEach((el) => {
      if (found.length > 400) return;
      const ids = el.getAttribute('data-source').split(/\s+/).filter(Boolean);
      if (el.tagName === 'TABLE' && el.hasAttribute('data-row-key')) { verifyTable(el, ids[0], found); return; }
      if (el.tagName === 'TH' || ids.length !== 1 || el.closest('template')) return;
      if (el.querySelector('[data-source]')) return;
      const shown = (el.textContent || '').trim().slice(0, 120);
      const r = cellMark(ids[0], el.getAttribute('data-field'), el.getAttribute('data-where'), el.getAttribute('data-row'), shown, el);
      if (r) found.push(r);
    });
    flagged = found.length;
    marks = found.slice(0, 20);
    scheduleReport();
  }
  function verifyTable(table, src, found) {
    const v = view(src);
    if (v.status !== 'ok' || !Array.isArray(v.data)) return;
    const keyCols = table.getAttribute('data-row-key').split(',').map((s) => s.trim()).filter(Boolean);
    const head = table.tHead ? table.tHead.rows[0] : table.rows[0];
    if (!head) return;
    const ths = [...head.cells].map((th) => (th.hasAttribute('data-field') ? th : null));
    const body = table.tBodies.length ? [...table.tBodies].flatMap((b) => [...b.rows]) : [...table.rows].slice(1);
    body.slice(0, 300).forEach((tr) => {
      const key = tableKeyOf(tr, keyCols, ths);
      const where = keyCols.map((k, i) => encodeURIComponent(k) + '=' + encodeURIComponent(key[i] == null ? '' : key[i])).join('&');
      ths.forEach((th, i) => {
        if (!th || !tr.cells[i]) return;
        const field = th.getAttribute('data-field');
        const thSrc = th.getAttribute('data-source') || src;
        const shown = tr.cells[i].textContent.trim().slice(0, 120);
        const r = cellMark(thSrc, field, where, null, shown, tr.cells[i]);
        if (r) { r.mark = describeMark(table) + ' td'; found.push(r); }
      });
    });
  }
  let verifyTimer = null;
  function scheduleVerify() { clearTimeout(verifyTimer); verifyTimer = setTimeout(verify, 600); }

  // ---------------------------------------------------------------- documento de corridas
  let reportTimer = null, lastReport = '';
  function scheduleReport() { clearTimeout(reportTimer); reportTimer = setTimeout(report, 1500); }
  function report() {
    const datasets = {};
    Object.entries(sources).forEach(([id, s]) => {
      if (s.status === 'idle' || s.status === 'loading') return;
      const d = { status: s.status === 'ok' ? 'ok' : s.failure || 'failed', at: s.at };
      if (s.message && s.status !== 'ok') d.message = s.message;
      if (s.rows !== undefined) d.rows = s.rows;
      if (s.cut) d.cut = true;
      if (s.ms !== undefined) d.ms = s.ms;
      datasets[id] = d;
    });
    Object.entries(calcs).forEach(([id, c]) => { if (c.error) datasets[id] = { status: 'failed', message: c.error, at: new Date().toISOString() }; });
    const doc = { params, datasets };
    if (marks.length) { doc.marks = marks; doc.flagged = flagged; }
    const errs = [...errors.values()];
    if (errs.length) doc.errors = errs.slice(0, 20);
    const sig = JSON.stringify([doc.params, Object.entries(datasets).map(([k, d]) => [k, d.status, d.message, d.rows]), doc.marks, doc.flagged, errs.map((e) => [e.kind, e.message, e.count])]);
    if (sig === lastReport) return;
    lastReport = sig;
    post('dash:runs', { doc });
  }

  // ---------------------------------------------------------------- estado para el shell (Fuentes)
  let stateTimer = null;
  function postState() {
    clearTimeout(stateTimer);
    stateTimer = setTimeout(() => {
      const src = {};
      Object.entries(sources).forEach(([id, s]) => {
        src[id] = { status: s.status === 'idle' ? 'loading' : s.status, message: s.message, refreshing: !!s.refreshing, at: s.at, stale: s.stale, rows: s.rows, cut: s.cut, ms: s.ms, failure: s.failure };
      });
      const cs = Object.entries(calcs).map(([id, c]) => ({ id, title: c.title || id, description: c.description || '', inputs: c.inputs, status: calcView(id).status, error: c.error }));
      post('dash:state', { sources: src, calcs: cs, loaders: Object.keys(loaders), params, defaults: DEFAULTS });
    }, 80);
  }

  /** Lo que muestra Fuentes de una fuente: filas, meta, argumentos con filtros, fórmula o loader. */
  function inspect(id) {
    if (calcs[id]) {
      const c = calcs[id], v = calcView(id);
      return { id, kind: 'calc', title: c.title || id, description: c.description || '', inputs: c.inputs, fn: c.fn.toString(), status: v.status, message: v.message, data: limit(v.data), columns: v.columns, meta: [] };
    }
    const s = sources[id];
    if (!DATASETS[id]) return { id, kind: 'missing', status: 'missing' };
    const out = { id, kind: DATASETS[id].kind, values: valuesFor(id), loader: loaders[id] ? { description: loaders[id].description || '', fn: loaders[id].fn.toString() } : null };
    if (s) Object.assign(out, { status: s.status, message: s.message, data: limit(s.data), columns: s.columns, meta: s.meta || [], at: s.at, stale: s.stale, rows: s.rows, cut: s.cut, args: s.args, refreshing: s.refreshing });
    else out.status = 'idle';
    return out;
  }
  function limit(data) { return Array.isArray(data) && data.length > 5000 ? data.slice(0, 5000) : data; }

  // ---------------------------------------------------------------- modo datos y selección
  document.addEventListener('click', (ev) => {
    if (mode !== 'data') return;
    ev.preventDefault();
    ev.stopPropagation();
    const target = ev.target instanceof Element ? ev.target : null;
    const sel = target ? describeSelection(target) : null;
    if (sel) post('dash:select', sel);
  }, true);
  function describeSelection(target) {
    const el = target.closest('[data-source]');
    if (!el || !root.contains(el)) return null;
    const ids = el.getAttribute('data-source').split(/\s+/).filter(Boolean);
    const host = el.closest('[id]');
    const sel = { ids, id: ids[0], at: host && root.contains(host) ? host.id : undefined };
    if (el.tagName === 'TABLE' && el.hasAttribute('data-row-key')) {
      const td = target.closest('td,th');
      const tr = target.closest('tr');
      const head = el.tHead ? el.tHead.rows[0] : el.rows[0];
      if (td && tr && head && tr !== head && head.cells[td.cellIndex] && head.cells[td.cellIndex].hasAttribute('data-field')) {
        const th = head.cells[td.cellIndex];
        const keyCols = el.getAttribute('data-row-key').split(',').map((s) => s.trim());
        const ths = [...head.cells].map((h) => (h.hasAttribute('data-field') ? h : null));
        const key = tableKeyOf(tr, keyCols, ths);
        sel.field = th.getAttribute('data-field');
        sel.where = keyCols.map((k, i) => encodeURIComponent(k) + '=' + encodeURIComponent(key[i] == null ? '' : key[i])).join('&');
        if (th.hasAttribute('data-source')) { sel.id = th.getAttribute('data-source'); sel.ids = [sel.id]; }
        sel.shown = td.textContent.trim().slice(0, 120);
      }
    } else if (el.hasAttribute('data-field') && ids.length === 1) {
      sel.field = el.getAttribute('data-field');
      if (el.hasAttribute('data-where')) sel.where = el.getAttribute('data-where');
      if (el.hasAttribute('data-row')) sel.row = Number(el.getAttribute('data-row'));
      sel.shown = (el.textContent || '').trim().slice(0, 120);
    } else if (calcs[ids[0]] && ids.length === 1) {
      sel.shown = (el.textContent || '').trim().slice(0, 120);
    }
    if (sel.shown !== undefined) {
      const v = view(sel.id);
      if (v.status === 'ok') {
        if (sel.field) {
          const rows = Array.isArray(v.data) ? v.data : [];
          const r = sel.row !== undefined ? rows[sel.row] : sel.where ? rowsWhere(rows, sel.where)[0] : rows.length === 1 ? rows[0] : undefined;
          if (r) { sel.value = r[sel.field]; sel.rowIndex = rows.indexOf(r); }
        } else if (!Array.isArray(v.data)) sel.value = v.data;
      }
    }
    return sel;
  }
  // Lo seleccionado en la página (texto marcado) también cuenta para el contexto de vista.
  document.addEventListener('selectionchange', () => {
    clearTimeout(selectionTimer);
    selectionTimer = setTimeout(() => {
      const s = document.getSelection();
      if (!s || s.isCollapsed || !s.anchorNode) { post('dash:selection', { ids: [] }); return; }
      const ids = new Set();
      let at;
      [s.anchorNode, s.focusNode].forEach((n) => {
        const el = n && (n.nodeType === 1 ? n : n.parentElement);
        if (!el || !root.contains(el)) return;
        const m = el.closest('[data-source]');
        if (m) m.getAttribute('data-source').split(/\s+/).forEach((i) => ids.add(i));
        const host = el.closest('[id]');
        if (host && root.contains(host) && !at) at = host.id;
      });
      post('dash:selection', { ids: [...ids].filter((i) => DATASETS[i]), at });
    }, 250);
  });
  let selectionTimer = null;

  // ---------------------------------------------------------------- API pública
  const COLORS = Object.freeze([1, 2, 3, 4, 5, 6, 7, 8].map((i) => `var(--ds-chart-${i})`));
  const dash = Object.freeze({
    data: (id) => view(String(id)),
    onData(fn) {
      if (typeof fn !== 'function') throw new TypeError('dash.onData(fn): fn es una función.');
      handlers.push(fn);
      if (booted) runHandler(fn);
    },
    calc(id, spec) {
      id = String(id);
      if (DATASETS[id]) throw new Error(`dash.calc: '${id}' ya es un dataset.`);
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) throw new Error(`dash.calc: id inválido '${id}'.`);
      const s = spec || {};
      if (typeof s.fn !== 'function' || !Array.isArray(s.inputs)) throw new Error('dash.calc(id, {inputs, fn, title, description}).');
      calcs[id] = { inputs: s.inputs.map(String), fn: s.fn, title: s.title, description: s.description, key: null };
      postState();
    },
    loader(id, spec) {
      id = String(id);
      const s = spec || {};
      if (!DATASETS[id]) throw new Error(`dash.loader: no existe el dataset '${id}'.`);
      if (typeof s.fn !== 'function') throw new Error('dash.loader(id, {description, fn}).');
      loaders[id] = { description: s.description, fn: s.fn };
    },
    params: () => clone(params),
    setParams,
    resetParams,
    refresh(id) { if (DATASETS[id]) load(String(id), 'refresh'); },
    setLink(name) {
      const n = name == null ? '' : String(name).slice(0, 120);
      post('dash:link', { name: n });
    },
    colors: COLORS,
  });
  window.dash = dash;

  // ---------------------------------------------------------------- montaje
  const DROP = 'iframe,frame,frameset,object,embed,form,base,meta,portal,applet';
  const OWN = /^[A-Za-z0-9_.-]{1,80}$/;
  function mount() {
    const html = FILES['index.html'];
    if (html == null) {
      root.innerHTML = '<div class="dash-empty">Este dashboard todavía no tiene página.</div>';
      return [];
    }
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    const frag = tpl.content;
    frag.querySelectorAll(DROP).forEach((el) => el.remove());
    const queue = [];
    frag.querySelectorAll('*').forEach((el) => {
      [...el.attributes].forEach((a) => {
        const n = a.name.toLowerCase();
        if (n.startsWith('on') || n === 'ping') el.removeAttribute(a.name);
        else if (/^(href|src|srcset|action|formaction|xlink:href|poster|background)$/.test(n) && /^\s*(javascript:|vbscript:|https?:|\/\/)/i.test(a.value)) el.removeAttribute(a.name);
      });
    });
    frag.querySelectorAll('link').forEach((el) => {
      const href = el.getAttribute('href') || '';
      if (OWN.test(href) && FILES[href] != null && /\.css$/i.test(href)) {
        const st = document.createElement('style');
        st.textContent = FILES[href];
        el.replaceWith(st);
      } else el.remove();
    });
    frag.querySelectorAll('script').forEach((el) => {
      const src = el.getAttribute('src');
      if (src !== null) {
        if (OWN.test(src) && FILES[src] != null) queue.push({ code: FILES[src], file: src, at: el });
        else el.remove();
      } else if (!el.type || /^(text|application)\/javascript$/i.test(el.type)) queue.push({ code: el.textContent || '', file: 'index.html', at: el });
      else el.remove();
    });
    // Los scripts se sacan del árbol y corren después, en orden, cada uno en su scope.
    queue.forEach((q) => q.at.remove());
    root.replaceChildren(frag);
    return queue;
  }
  function runScript(code, file) {
    const el = document.createElement('script');
    if (NONCE) el.nonce = NONCE;
    el.textContent = '(function (dash, d3) { try {\n' + code + '\n} catch (e) { window.__dashStopped(e, ' + JSON.stringify(file) + '); } }).call(window, window.dash, window.d3);';
    currentFile = file;
    try { document.body.appendChild(el); } finally { currentFile = null; el.remove(); }
  }

  function boot() {
    const queue = mount();
    queue.forEach((q) => runScript(q.code, q.file));
    // Marcas fuera de un <template> cargan su fuente, aunque estén ocultas.
    root.querySelectorAll('[data-source]').forEach((el) => el.getAttribute('data-source').split(/\s+/).forEach((id) => id && want(id)));
    booted = true;
    Object.keys(sources).forEach((id) => { if (sources[id].status === 'loading') load(id, 'prefer'); });
    // Las marcas que aparecen después (un <template> clonado, una pestaña) cargan lo suyo.
    new MutationObserver((muts) => {
      let added = false;
      muts.forEach((m) => m.addedNodes.forEach((n) => {
        if (n.nodeType !== 1) return;
        const els = n.matches('[data-source]') ? [n] : [];
        els.concat([...n.querySelectorAll('[data-source]')]).forEach((el) => el.getAttribute('data-source').split(/\s+/).forEach((id) => {
          if (id && (DATASETS[id] || calcs[id]) && (!sources[id] || sources[id].status === 'idle')) { want(id); added = true; }
        }));
      }));
      if (added) { postState(); schedule(); }
    }).observe(root, { childList: true, subtree: true });
    draw();
    postState();
    post('dash:ready', {});
  }

  boot();
})();
