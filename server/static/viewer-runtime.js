/* Visor fijo de artifacts: corre dentro de un iframe sandbox sin red.
 *
 * Provee la API `dash`, monta la página del artifact en #dash-root, pide los
 * datos al shell padre (que media la consulta), verifica los valores marcados
 * con data-source/data-field y reporta corridas.
 */
(function () {
  'use strict';
  const CFG = window.__DASH__;
  const NONCE = document.currentScript && document.currentScript.nonce;
  const PARENT = window.parent;
  const root = document.getElementById('dash-root');
  const filtersEl = document.getElementById('dash-filters');
  const statusEl = document.getElementById('dash-status');
  const LOCALE = CFG.locale || 'es-AR';

  const datasets = CFG.datasets || {};
  const specs = CFG.params || [];
  const params = {};
  specs.forEach((p) => { params[p.name] = p.default === undefined ? null : p.default; });

  const store = {};      // id -> filas (array con .columns y .meta)
  const failed = {};     // id -> mensaje de error
  const calcs = {};      // id -> {deps, fn, formula}
  const handlers = [];
  const waiters = {};
  const record = { datasets_loaded: [], source_failures: [], mismatches: [], errors: [], removed: [], unmarked_numbers: 0 };
  let rendered = false;

  // ---------------------------------------------------------------- transporte
  let seq = 0;
  const pending = new Map();
  function request(dataset, values, cache) {
    return new Promise((resolve, reject) => {
      const reqId = ++seq;
      pending.set(reqId, { resolve, reject });
      PARENT.postMessage({ type: 'dash:fetch', reqId, dataset, params: values, cache }, '*');
      setTimeout(() => {
        if (pending.has(reqId)) { pending.delete(reqId); reject(new Error('Tiempo de espera agotado')); }
      }, 330000);
    });
  }
  function post(type, payload) { PARENT.postMessage(Object.assign({ type }, payload), '*'); }

  window.addEventListener('message', (ev) => {
    if (ev.source !== PARENT) return;
    const m = ev.data || {};
    if (m.type === 'dash:data' && pending.has(m.reqId)) {
      const p = pending.get(m.reqId);
      pending.delete(m.reqId);
      m.ok ? p.resolve(m.payload) : p.reject(new Error(m.error || 'Error de la fuente'));
    } else if (m.type === 'dash:theme') {
      document.documentElement.setAttribute('data-theme', m.theme);
      emit();
    } else if (m.type === 'dash:highlight') {
      root.querySelectorAll('.dash-hl').forEach((el) => el.classList.remove('dash-hl'));
      if (m.dataset) root.querySelectorAll(`[data-source="${CSS.escape(m.dataset)}"]`).forEach((el) => el.classList.add('dash-hl'));
    } else if (m.type === 'dash:reload') {
      // Sin dataset: todas las fuentes en vivo. Con dataset: solo esa.
      loadAll(m.dataset ? [m.dataset].filter((id) => id in datasets) : Object.keys(datasets), 'refresh');
    } else if (m.type === 'dash:mode') {
      // Modo "de dónde salen los números": los valores marcados se resaltan y abren su fuente.
      document.body.classList.toggle('dash-sources-mode', m.mode === 'sources');
    }
  });

  // ---------------------------------------------------------------- errores
  function logError(message, extra) {
    record.errors.push(Object.assign({ message: String(message).slice(0, 500) }, extra || {}));
    scheduleVerify();
  }
  window.addEventListener('error', (e) => logError(e.message, { line: e.lineno, col: e.colno }));
  window.addEventListener('unhandledrejection', (e) => logError((e.reason && e.reason.message) || e.reason));

  // ---------------------------------------------------------------- formatos
  const nf = (o) => new Intl.NumberFormat(LOCALE, o);
  function parseDate(v) {
    if (v instanceof Date) return v;
    const s = String(v);
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); }
    return new Date(s);
  }
  const fmt = {
    number: (v, d = 0) => (v == null ? '—' : nf({ minimumFractionDigits: d, maximumFractionDigits: d }).format(v)),
    int: (v) => fmt.number(v, 0),
    compact: (v, d = 1) => (v == null ? '—' : nf({ notation: 'compact', maximumFractionDigits: d }).format(v)),
    pct: (v, d = 1) => (v == null ? '—' : nf({ style: 'percent', minimumFractionDigits: d, maximumFractionDigits: d }).format(v)),
    delta: (v, d = 1) => (v == null ? '—' : nf({ style: 'percent', signDisplay: 'exceptZero', minimumFractionDigits: d, maximumFractionDigits: d }).format(v)),
    money: (v, cur = 'ARS', d = 0) => (v == null ? '—' : nf({ style: 'currency', currency: cur, minimumFractionDigits: d, maximumFractionDigits: d }).format(v)),
    date: (v, style = 'short') => {
      if (v == null) return '—';
      const opts = { short: { day: '2-digit', month: '2-digit', year: 'numeric' }, long: { day: 'numeric', month: 'long', year: 'numeric' },
        month: { month: 'short', year: 'numeric' }, day: { day: 'numeric', month: 'short' } }[style] || {};
      return parseDate(v).toLocaleDateString(LOCALE, opts);
    },
  };

  // ---------------------------------------------------------------- colores
  const categoryOrder = new Map();
  function theme() {
    const attr = document.documentElement.getAttribute('data-theme');
    return attr === 'dark' || (attr !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }
  function colors() {
    const t = CFG.tokens[theme()];
    return Object.assign({}, t, {
      theme: theme(),
      status: CFG.tokens.status,
      sequential: CFG.tokens.sequential,
      diverging: CFG.tokens.diverging,
      // El color sigue a la entidad, nunca a su posición: una clave conserva su slot.
      category(key) {
        const k = String(key);
        if (!categoryOrder.has(k)) categoryOrder.set(k, categoryOrder.size);
        const i = categoryOrder.get(k);
        return i < t.series.length ? t.series[i] : t.muted;
      },
      seqScale(domain) {
        return d3.scaleSequential(d3.interpolateRgbBasis(CFG.tokens.sequential)).domain(domain);
      },
    });
  }

  // ---------------------------------------------------------------- tooltip y tabla
  const tip = document.createElement('div');
  tip.className = 'dash-tooltip';
  tip.hidden = true;
  document.body.appendChild(tip);
  const tooltip = {
    show(event, title, rows) {
      tip.replaceChildren();
      if (title) { const h = document.createElement('div'); h.className = 'dash-tooltip-title'; h.textContent = title; tip.appendChild(h); }
      (rows || []).forEach((r) => {
        const line = document.createElement('div'); line.className = 'dash-tooltip-row';
        if (r.color) { const k = document.createElement('span'); k.className = 'dash-key'; k.style.background = r.color; line.appendChild(k); }
        const v = document.createElement('strong'); v.textContent = r.value; line.appendChild(v);
        const l = document.createElement('span'); l.textContent = r.label || ''; line.appendChild(l);
        tip.appendChild(line);
      });
      tip.hidden = false;
      const pad = 12, w = tip.offsetWidth, h = tip.offsetHeight;
      let x = event.clientX + pad, y = event.clientY + pad;
      if (x + w > innerWidth - 4) x = event.clientX - w - pad;
      if (y + h > innerHeight - 4) y = event.clientY - h - pad;
      tip.style.left = Math.max(4, x) + 'px';
      tip.style.top = Math.max(4, y) + 'px';
    },
    hide() { tip.hidden = true; },
  };

  /** Leyenda: una fila que se acomoda sola (envuelve, nunca se pisa con el gráfico). */
  function legend(target, items) {
    const el = typeof target === 'string' ? root.querySelector(target) : target;
    if (!el) throw new Error('dash.legend: no existe ' + target);
    const box = document.createElement('div');
    box.className = 'dash-legend';
    (items || []).forEach((it) => {
      const item = document.createElement('span');
      const key = document.createElement('span');
      key.className = it.type === 'line' ? 'dash-key' : 'dash-swatch';
      key.style.background = it.color;
      const label = document.createElement('span');
      label.textContent = it.label;
      item.append(key, label);
      box.appendChild(item);
    });
    el.replaceChildren(box);
    return box;
  }

  function table(target, rows, opts) {
    opts = opts || {};
    const el = typeof target === 'string' ? root.querySelector(target) : target;
    if (!el) throw new Error('dash.table: no existe ' + target);
    const cols = opts.columns || (rows.columns || Object.keys(rows[0] || {})).map((f) => ({ field: f }));
    const t = document.createElement('table'); t.className = 'dash-table';
    const thead = t.createTHead().insertRow();
    cols.forEach((c) => { const th = document.createElement('th'); th.textContent = c.label || c.field; if (c.align === 'right' || c.format) th.className = 'num'; thead.appendChild(th); });
    const body = t.createTBody();
    rows.slice(0, opts.limit || 500).forEach((r, i) => {
      const tr = body.insertRow();
      cols.forEach((c) => {
        const td = tr.insertCell();
        const v = r[c.field];
        td.textContent = c.format ? c.format(v, r) : v == null ? '—' : String(v);
        if (c.format || typeof v === 'number') td.className = 'num';
        if (opts.source) { td.dataset.source = opts.source; td.dataset.field = c.field; td.dataset.row = String(i); }
      });
    });
    el.replaceChildren(t);
    return t;
  }

  // ---------------------------------------------------------------- datos
  function pick(obj, keys) { const o = {}; (keys || []).forEach((k) => { o[k] = obj[k]; }); return o; }

  function settle(id) {
    (waiters[id] || []).forEach((w) => (failed[id] ? w.reject(new Error(failed[id])) : w.resolve(store[id])));
    delete waiters[id];
  }

  // Estado de cada fuente para el panel Fuentes del shell.
  const statuses = {};
  function reportStatus(id, status) {
    statuses[id] = status;
    post('dash:status', { statuses });
  }

  async function fetchDataset(id, cache) {
    reportStatus(id, Object.assign({}, statuses[id], { loading: true }));
    try {
      const payload = await request(id, pick(params, datasets[id].params), cache);
      const rows = payload.rows || [];
      rows.columns = (payload.columns || []).map((c) => c.name);
      rows.meta = { cached_at: payload.cached_at, stale: payload.stale, truncated: payload.truncated, total_rows: payload.total_rows, mode: payload.mode };
      store[id] = rows;
      delete failed[id];
      if (!record.datasets_loaded.includes(id)) record.datasets_loaded.push(id);
      reportStatus(id, { loading: false, cached_at: payload.cached_at, stale: !!payload.stale, rows: payload.total_rows, truncated: !!payload.truncated, mode: payload.mode, params_used: payload.params_used || null });
      record.source_failures = record.source_failures.filter((f) => f.dataset !== id);
      settle(id);
      return payload;
    } catch (e) {
      failed[id] = e.message || String(e);
      reportStatus(id, { loading: false, error: failed[id] });
      record.source_failures = record.source_failures.filter((f) => f.dataset !== id);
      record.source_failures.push({ dataset: id, error: failed[id] });
      settle(id);
      throw e;
    }
  }

  function computeCalcs() {
    Object.entries(calcs).forEach(([id, c]) => {
      if (!c.deps.every((d) => store[d])) return;
      try {
        const rows = c.fn.apply(null, c.deps.map((d) => store[d])) || [];
        rows.columns = rows.columns || Object.keys(rows[0] || {});
        rows.meta = { calc: true };
        store[id] = rows;
        delete failed[id];
      } catch (e) {
        failed[id] = 'dash.calc: ' + e.message;
        logError(failed[id], { calc: id });
      }
      settle(id);
    });
  }

  async function loadAll(ids, cache) {
    if (!ids.length) { emit(); return; }
    if (rendered) root.classList.add('dash-loading');
    const results = await Promise.allSettled(ids.map((id) => fetchDataset(id, cache)));
    computeCalcs();
    emit();
    const stale = results.filter((r) => r.status === 'fulfilled' && r.value.stale).map((r) => r.value.dataset);
    if (cache === 'prefer' && stale.length) loadAll(stale, 'refresh');
  }

  function emit() {
    const snapshot = Object.assign({}, store);
    handlers.forEach((fn) => {
      try { fn(snapshot); } catch (e) { logError(e.message, { handler: true, stack: String(e.stack || '').slice(0, 300) }); }
    });
    rendered = true;
    root.classList.remove('dash-loading');
    scheduleVerify();
  }

  function setParams(partial) {
    const changed = Object.keys(partial || {}).filter((k) => k in params && JSON.stringify(params[k]) !== JSON.stringify(partial[k]));
    changed.forEach((k) => { params[k] = partial[k]; });
    syncFilters();
    post('dash:params', { params });
    if (!changed.length) return;
    const affected = Object.keys(datasets).filter((id) => (datasets[id].params || []).some((p) => changed.includes(p)));
    loadAll(affected, 'auto');
  }

  // ---------------------------------------------------------------- verificación
  function numberCandidates(text) {
    const s = text.replace(/ | /g, ' ');
    const m = s.match(/[-−]?\d[\d.,]*/);
    if (!m) return [];
    const raw = m[0].replace('−', '-');
    const out = new Set();
    const decimals = (str, sep) => (str.includes(sep) ? str.split(sep).pop().length : 0);
    // es-AR: punto miles, coma decimal
    out.add(JSON.stringify([Number(raw.replace(/\./g, '').replace(',', '.')), decimals(raw, ',')]));
    // en: coma miles, punto decimal
    out.add(JSON.stringify([Number(raw.replace(/,/g, '')), decimals(raw, '.')]));
    let mult = 1;
    const rest = s.slice(m.index + m[0].length).toLowerCase();
    if (/^\s*(mil\b|k\b)/.test(rest)) mult = 1e3;
    else if (/^\s*(m\b|mill|mm\b)/.test(rest)) mult = 1e6;
    else if (/^\s*(b\b|mil m|bn)/.test(rest)) mult = 1e9;
    const pct = /%/.test(s);
    return [...out].map((j) => JSON.parse(j)).filter(([n]) => isFinite(n)).map(([n, d]) => ({ n: n * mult, tol: 0.5 * Math.pow(10, -d) * mult, pct }));
  }

  function matches(shown, value) {
    if (value == null) return /^(—|-|n\/d|s\/d|sin datos|null|)$/i.test(shown.trim());
    if (typeof value === 'number') {
      return numberCandidates(shown).some(({ n, tol, pct }) => {
        const tries = pct ? [[n / 100, tol / 100], [n, tol]] : [[n, tol]];
        return tries.some(([x, t]) => Math.abs(x - value) <= t + 1e-9 || Math.abs(x - value) <= Math.abs(value) * 0.005);
      });
    }
    if (typeof value === 'boolean') return /^(sí|si|true|no|false|✓|✗)$/i.test(shown.trim());
    const sv = String(value);
    if (shown.includes(sv)) return true;
    if (/^\d{4}-\d{2}-\d{2}/.test(sv)) return ['short', 'long', 'month', 'day'].some((st) => shown.includes(fmt.date(sv.slice(0, 10), st)));
    return false;
  }

  let verifyTimer = null;
  let lastReport = '';
  function scheduleVerify() { clearTimeout(verifyTimer); verifyTimer = setTimeout(verify, 700); }

  function verify() {
    const mismatches = [];
    root.querySelectorAll('[data-source]').forEach((el) => {
      const src = el.getAttribute('data-source');
      const field = el.getAttribute('data-field');
      const shown = (el.textContent || '').trim().slice(0, 80);
      if (!(src in datasets) && !(src in calcs)) { mismatches.push({ dataset: src, field, shown, problem: 'fuente inexistente' }); return; }
      if (!field || !shown || el.querySelector('[data-source]')) return;
      const rows = store[src];
      if (!rows) return;
      let candidates = rows;
      const rowAttr = el.getAttribute('data-row');
      const keyAttr = el.getAttribute('data-key');
      if (rowAttr != null) candidates = [rows[Number(rowAttr)]];
      else if (keyAttr && keyAttr.includes('=')) {
        const [k, v] = [keyAttr.slice(0, keyAttr.indexOf('=')), keyAttr.slice(keyAttr.indexOf('=') + 1)];
        candidates = rows.filter((r) => String(r[k]) === v);
      }
      candidates = candidates.filter(Boolean);
      if (!candidates.length) { mismatches.push({ dataset: src, field, shown, problem: 'fila no encontrada' }); return; }
      if (!(field in candidates[0])) { mismatches.push({ dataset: src, field, shown, problem: 'la columna no existe' }); return; }
      if (!candidates.some((r) => matches(shown, r[field]))) {
        mismatches.push({ dataset: src, field, shown, expected: candidates.length === 1 ? candidates[0][field] : `${candidates.length} filas, ninguna coincide`, problem: 'no coincide con los datos' });
      }
    });
    record.mismatches = mismatches.slice(0, 50);

    let unmarked = 0;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const parent = node.parentElement;
      if (!parent || parent.closest('[data-source],script,style,.tick,[data-ignore],[aria-hidden="true"]')) continue;
      if (/(?<![\w#])(?!(?:19|20)\d\d\b)\d[\d.,]*\d|\b\d%/.test(node.nodeValue)) unmarked++;
    }
    record.unmarked_numbers = unmarked;

    const issues = record.mismatches.length + record.errors.length + record.source_failures.length;
    // Los problemas los muestra la barra del visor (shell); acá no se dibuja nada.
    statusEl.hidden = true;
    const report = JSON.stringify(record);
    if (report !== lastReport) { lastReport = report; post('dash:run', { record }); }
  }

  // ---------------------------------------------------------------- filtros
  const controls = {};
  function syncFilters() {
    Object.entries(controls).forEach(([name, c]) => c.set(params[name]));
  }
  function buildFilters() {
    if (!specs.length) { filtersEl.hidden = true; return; }
    specs.forEach((p) => {
      const wrap = document.createElement('label');
      wrap.className = 'dash-filter';
      const span = document.createElement('span');
      span.textContent = p.label || p.name;
      wrap.appendChild(span);
      let input;
      const typ = p.type || 'text';
      if (typ === 'select' || typ === 'multi') {
        input = document.createElement('select');
        if (typ === 'multi') input.multiple = true;
        const fill = (values) => {
          const current = params[p.name];
          input.replaceChildren();
          if (typ === 'select') { const o = document.createElement('option'); o.value = ''; o.textContent = 'Todos'; input.appendChild(o); }
          values.forEach((v) => { const o = document.createElement('option'); o.value = String(v); o.textContent = String(v); input.appendChild(o); });
          controls[p.name].set(current);
        };
        controls[p.name] = {
          set(v) {
            const vals = (Array.isArray(v) ? v : v == null ? [] : [v]).map(String);
            [...input.options].forEach((o) => { o.selected = vals.includes(o.value) || (!vals.length && o.value === '' && typ === 'select'); });
          },
        };
        if (Array.isArray(p.options)) fill(p.options);
        else if (p.options && p.options.dataset) {
          dash.data(p.options.dataset)
            .then((rows) => fill([...new Set(rows.map((r) => r[p.options.field]).filter((v) => v != null))].sort()))
            .catch((e) => logError(`Opciones de ${p.name}: ${e.message}`));
        }
        input.addEventListener('change', () => {
          const sel = [...input.selectedOptions].map((o) => o.value).filter((v) => v !== '');
          setParams({ [p.name]: typ === 'multi' ? sel : sel[0] || null });
        });
      } else if (typ === 'boolean') {
        input = document.createElement('input');
        input.type = 'checkbox';
        controls[p.name] = { set(v) { input.checked = !!v; } };
        input.addEventListener('change', () => setParams({ [p.name]: input.checked }));
      } else {
        input = document.createElement('input');
        input.type = typ === 'date' ? 'date' : typ === 'number' ? 'number' : 'search';
        controls[p.name] = { set(v) { input.value = v == null ? '' : String(v); } };
        let t;
        input.addEventListener(typ === 'text' ? 'input' : 'change', () => {
          clearTimeout(t);
          t = setTimeout(() => {
            const v = input.value === '' ? null : typ === 'number' ? Number(input.value) : input.value;
            setParams({ [p.name]: v });
          }, typ === 'text' ? 500 : 0);
        });
      }
      if (p.help) wrap.title = p.help;
      wrap.appendChild(input);
      filtersEl.appendChild(wrap);
      controls[p.name] && controls[p.name].set(params[p.name]);
    });
  }

  // ---------------------------------------------------------------- API pública
  const dash = {
    get params() { return Object.assign({}, params); },
    get colors() { return colors(); },
    fmt,
    tooltip,
    table,
    legend,
    data(id) {
      if (store[id]) return Promise.resolve(store[id]);
      if (failed[id]) return Promise.reject(new Error(failed[id]));
      if (!(id in datasets) && !(id in calcs)) return Promise.reject(new Error(`No existe la fuente '${id}'`));
      return new Promise((resolve, reject) => { (waiters[id] = waiters[id] || []).push({ resolve, reject }); });
    },
    onData(fn) {
      handlers.push(fn);
      if (rendered) { try { fn(Object.assign({}, store)); } catch (e) { logError(e.message); } }
    },
    calc(id, deps, fn) {
      if (!/^[a-z][a-z0-9_]*$/.test(id) || id in datasets) throw new Error(`dash.calc: id inválido o repetido '${id}'`);
      calcs[id] = { deps, fn, formula: fn.toString() };
      post('dash:calcs', { calcs: Object.entries(calcs).map(([k, c]) => ({ id: k, deps: c.deps, formula: c.formula })) });
      computeCalcs();
    },
    setParams,
    loader(target, on = true) {
      const el = typeof target === 'string' ? root.querySelector(target) : target;
      if (el) el.classList.toggle('dash-busy', !!on);
    },
    failed(id) { return failed[id] || null; },
  };
  Object.freeze(dash.fmt);
  window.dash = dash;

  // ---------------------------------------------------------------- montaje (saneado forzado)
  const BAD_TAGS = 'iframe,frame,frameset,object,embed,form,base,meta,link,portal,applet';
  const BAD_SCRIPT = [
    /\blocalStorage\b|\bsessionStorage\b|\bindexedDB\b|document\.cookie/,
    /\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|\bimport\s*\(/,
    /\bwindow\.(open|top|parent|opener)\b|(?<![\w.$])(top|parent)\.|\bpostMessage\b|\.opener\b/,
  ];
  function mount(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    doc.querySelectorAll(BAD_TAGS).forEach((el) => { record.removed.push(`<${el.tagName.toLowerCase()}>`); el.remove(); });
    doc.querySelectorAll('*').forEach((el) => {
      [...el.attributes].forEach((a) => {
        const n = a.name.toLowerCase();
        if (n.startsWith('on') || (/^(href|src|action|formaction|xlink:href|srcset)$/.test(n) && /^\s*(javascript:|https?:|\/\/)/i.test(a.value))) {
          record.removed.push(`${n}= en <${el.tagName.toLowerCase()}>`);
          el.removeAttribute(a.name);
        }
      });
    });
    const scripts = [];
    doc.querySelectorAll('script').forEach((s) => {
      const code = s.textContent || '';
      if (s.src || s.type === 'module' || BAD_SCRIPT.some((re) => re.test(code))) record.removed.push('<script> no permitido');
      else scripts.push(code);
      s.remove();
    });
    doc.querySelectorAll('style').forEach((s) => {
      if (/@import|url\(\s*['"]?\s*(https?:|\/\/)/i.test(s.textContent)) { record.removed.push('<style> con recursos externos'); s.remove(); }
    });
    const frag = document.createDocumentFragment();
    [...doc.head.querySelectorAll('style'), ...doc.body.childNodes].forEach((n) => frag.appendChild(document.importNode(n, true)));
    root.replaceChildren(frag);
    scripts.forEach((code) => {
      const el = document.createElement('script');
      if (NONCE) el.nonce = NONCE;
      el.textContent = code;
      root.appendChild(el);
    });
  }

  // Redibujar cuando cambia el ancho (panel redimensionado, ventana, celular girado):
  // los handlers de dash.onData se vuelven a llamar con los mismos datos, así los
  // gráficos se recalculan al ancho real en vez de escalarse como una imagen.
  let lastWidth = 0;
  let resizeTimer = null;
  new ResizeObserver(() => {
    const w = Math.round(root.clientWidth);
    if (!rendered || Math.abs(w - lastWidth) < 2) {
      lastWidth = w;
      return;
    }
    lastWidth = w;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(emit, 120);
  }).observe(root);

  buildFilters();
  if (!CFG.page) {
    root.innerHTML = '<div class="dash-empty">Este artifact todavía no tiene página.</div>';
  } else {
    mount(CFG.page);
  }
  // En modo fuentes, un clic sobre un valor marcado abre su fuente en vez de interactuar.
  document.addEventListener('click', (ev) => {
    if (!document.body.classList.contains('dash-sources-mode')) return;
    const el = ev.target instanceof Element ? ev.target.closest('[data-source]') : null;
    ev.preventDefault();
    ev.stopPropagation();
    if (el) post('dash:select', { dataset: el.getAttribute('data-source'), field: el.getAttribute('data-field') });
  }, true);

  post('dash:ready', {});
  loadAll(Object.keys(datasets), 'prefer');
})();
