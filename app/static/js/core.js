// Ortak yardımcılar: API, durum, biçimlendirme, grafikler, iş izleyici

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ------------------------------------------------------------------ API
async function req(method, url, body, isForm) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opt.body = body;
    else { opt.body = JSON.stringify(body); opt.headers['Content-Type'] = 'application/json'; }
  }
  const r = await fetch(url, opt);
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) {
    const msg = (data && data.detail) ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : `${r.status} ${r.statusText}`;
    throw new Error(msg);
  }
  return data;
}
export const api = {
  get: (u) => req('GET', u),
  post: (u, b) => req('POST', u, b ?? {}),
  del: (u) => req('DELETE', u),
  upload: (u, file) => { const f = new FormData(); f.append('file', file); return req('POST', u, f, true); },
};

// ------------------------------------------------------------------ durum
const LS_KEY = 'deltawing.state.v2';
export const state = {
  meta: null,
  cfg: null,           // aktif tasarım
  external: null,      // {id, name, transform, aref, lref, cofr, metrics}
  cfdSource: 'wing',
  lastQuick: null,
  env: null,
  activeJobs: {},      // sayfa -> job id
};
export function loadState() {
  try {
    const s = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
    if (s.cfg) state.cfg = s.cfg;
    if (s.external) state.external = s.external;
    if (s.activeJobs) state.activeJobs = s.activeJobs;
    if (s.cfdSource) state.cfdSource = s.cfdSource;
  } catch (e) { /* depolama kapalı olabilir */ }
}
export function saveState() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({ cfg: state.cfg, external: state.external, activeJobs: state.activeJobs, cfdSource: state.cfdSource }));
  } catch (e) { /* yoksay */ }
}
export const clone = (o) => JSON.parse(JSON.stringify(o));

// ------------------------------------------------------------------ ui
export function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'bad' ? 7000 : 3500);
}
export function modal(html) {
  $('#modal-body').innerHTML = html;
  $('#modal').classList.remove('hidden');
  return $('#modal-body');
}
export function closeModal() { $('#modal').classList.add('hidden'); $('#modal-body').innerHTML = ''; }
document.addEventListener('click', (e) => { if (e.target.id === 'modal' || e.target.closest('[data-close-modal]')) closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

export function fmt(v, nd = 4) {
  if (v === null || v === undefined || (typeof v === 'number' && !isFinite(v))) return '–';
  if (typeof v === 'boolean') return v ? 'Evet' : 'Hayır';
  if (typeof v !== 'number') return esc(v);
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return v.toExponential(2);
  return Number(v.toPrecision(nd)).toLocaleString('tr-TR', { maximumFractionDigits: 6 });
}
export const fmtTime = (s) => {
  s = Math.round(s || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  return h ? `${h} sa ${m} dk` : m ? `${m} dk ${ss} sn` : `${ss} sn`;
};
export const fmtDate = (t) => new Date(t * 1000).toLocaleString('tr-TR', { dateStyle: 'medium', timeStyle: 'short' });
export function debounce(fn, ms = 300) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

export const ICONS = {
  dashboard: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
  design: '<svg viewBox="0 0 24 24"><path d="M12 3 21 20 12 17 3 20Z"/></svg>',
  flow: '<svg viewBox="0 0 24 24"><path d="M3 7c4 0 5-3 9-3s5 3 9 3M3 12c4 0 5-3 9-3s5 3 9 3"/><path d="M4 18a8 3 0 0 0 16 0"/><circle cx="12" cy="17" r="1.6"/></svg>',
  quick: '<svg viewBox="0 0 24 24"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>',
  cfd: '<svg viewBox="0 0 24 24"><path d="M3 8c3-2 6 2 9 0s6-2 9 0M3 13c3-2 6 2 9 0s6-2 9 0M3 18c3-2 6 2 9 0s6-2 9 0"/></svg>',
  external: '<svg viewBox="0 0 24 24"><path d="M12 3 3 8v8l9 5 9-5V8z"/><path d="m3 8 9 5 9-5M12 13v8"/></svg>',
  optimize: '<svg viewBox="0 0 24 24"><path d="M3 20h18M6 16l4-5 3 3 5-7"/><circle cx="18" cy="7" r="1.5"/></svg>',
  results: '<svg viewBox="0 0 24 24"><path d="M4 4h16v16H4zM4 9h16M9 9v11"/></svg>',
  setup: '<svg viewBox="0 0 24 24"><path d="M21 16V8l-9-5-9 5v8l9 5z"/><path d="M12 12 3 7.5M12 12l9-4.5M12 12v9"/></svg>',
  settings: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M7 4v16l13-8z"/></svg>',
  stop: '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',
  download: '<svg viewBox="0 0 24 24"><path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v3h16v-3"/></svg>',
  upload: '<svg viewBox="0 0 24 24"><path d="M12 21V9m0 0 4 4m-4-4-4 4M4 7V4h16v3"/></svg>',
  save: '<svg viewBox="0 0 24 24"><path d="M5 3h11l3 3v15H5z"/><path d="M8 3v5h8M8 21v-7h8v7"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3 6h6l2 2h10v11H3z"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
  report: '<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M9 12h7M9 16h7M9 8h3"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5L20 7"/></svg>',
  chart: '<svg viewBox="0 0 24 24"><path d="M4 20V4M4 20h16M8 16l3-4 3 2 5-7"/></svg>',
};
export const icon = (n) => ICONS[n] || '';

export function empty(text, ic = 'chart') {
  return `<div class="empty">${icon(ic)}<div>${text}</div></div>`;
}

// ------------------------------------------------------------------ grafikler
export const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
export const SERIES = () => ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s7', '--s8'].map(css);

export function baseLayout(extra = {}) {
  const ink = css('--text'), ink2 = css('--text-2'), grid = css('--grid');
  const axis = { gridcolor: grid, zerolinecolor: grid, linecolor: css('--border-strong'), tickcolor: grid,
                 tickfont: { color: ink2, size: 11 }, title: { font: { color: ink2, size: 12 } }, automargin: true };
  const lay = {
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    font: { family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', color: ink, size: 12 },
    margin: { l: 56, r: 16, t: 28, b: 44 },
    xaxis: { ...axis }, yaxis: { ...axis },
    legend: { orientation: 'h', y: 1.12, x: 0, font: { color: ink2, size: 11 }, bgcolor: 'rgba(0,0,0,0)' },
    hoverlabel: { bgcolor: css('--surface'), bordercolor: css('--border-strong'), font: { color: ink } },
    hovermode: 'closest',
  };
  for (const [k, v] of Object.entries(extra)) {
    if (typeof v === 'object' && v && !Array.isArray(v) && lay[k] && typeof lay[k] === 'object') lay[k] = { ...lay[k], ...v, title: { ...(lay[k].title || {}), ...(typeof v.title === 'string' ? { text: v.title } : (v.title || {})) } };
    else lay[k] = v;
  }
  return lay;
}
export const plotConfig = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'] };

export function plot(el, traces, layout = {}) {
  if (typeof el === 'string') el = $(el);
  if (!el) return;
  if (el.querySelector(':scope > .empty, :scope > .alert')) el.innerHTML = '';
  Plotly.react(el, traces, baseLayout(layout), plotConfig);
}
export function line(x, y, name, i = 0, extra = {}) {
  return { x, y, name, type: 'scatter', mode: 'lines+markers', line: { color: SERIES()[i % 8], width: 2 },
           marker: { size: 8, color: SERIES()[i % 8], line: { color: css('--surface'), width: 2 } }, ...extra };
}

// 4'lü polar paneli
export function polarCharts(container, seriesList) {
  container.innerHTML = `<div class="grid g2">
    <div class="card"><h2>Kaldırma katsayısı <span class="sub">CL – α</span></h2><div class="chart" data-p="cl"></div></div>
    <div class="card"><h2>Sürükleme katsayısı <span class="sub">CD – α</span></h2><div class="chart" data-p="cd"></div></div>
    <div class="card"><h2>Sürükleme polari <span class="sub">CL – CD</span></h2><div class="chart" data-p="polar"></div></div>
    <div class="card"><h2>Kaldırma / sürükleme <span class="sub">L/D – α</span></h2><div class="chart" data-p="ld"></div></div></div>`;
  const P = (k) => container.querySelector(`[data-p="${k}"]`);
  const mk = (xk, yk) => seriesList.map((s, i) => {
    const r = [...s.rows].sort((a, b) => a.alpha_deg - b.alpha_deg);
    return line(r.map((d) => d[xk]), r.map((d) => d[yk]), s.name, s.color ?? i, s.dash ? { line: { color: SERIES()[(s.color ?? i) % 8], width: 2, dash: 'dash' } } : {});
  });
  const show = seriesList.length > 1;
  plot(P('cl'), mk('alpha_deg', 'CL'), { xaxis: { title: 'α [°]' }, yaxis: { title: 'CL' }, showlegend: show });
  plot(P('cd'), mk('alpha_deg', 'CD'), { xaxis: { title: 'α [°]' }, yaxis: { title: 'CD' }, showlegend: show });
  plot(P('polar'), mk('CD', 'CL'), { xaxis: { title: 'CD' }, yaxis: { title: 'CL' }, showlegend: show });
  plot(P('ld'), mk('alpha_deg', 'L_over_D'), { xaxis: { title: 'α [°]' }, yaxis: { title: 'L/D' }, showlegend: show });
}

export function resultsTable(rows, extraCols = []) {
  const cols = [['alpha_deg', 'α [°]'], ['CL', 'CL'], ['CD', 'CD'], ['CM', 'Cm'], ['lift_N', 'Kaldırma [N]'],
    ['drag_N', 'Sürükleme [N]'], ['L_over_D', 'L/D'], ...extraCols].filter(([k]) => rows.some((r) => r[k] !== undefined && r[k] !== null));
  return `<div class="tbl-wrap"><table class="tbl"><thead><tr>${cols.map(([, l]) => `<th class="num">${l}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${cols.map(([k]) => `<td class="num">${fmt(r[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

export function downloadCSV(rows, name) {
  if (!rows?.length) return;
  const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const csv = [keys.join(','), ...rows.map((r) => keys.map((k) => (r[k] ?? '')).join(','))].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = name;
  a.click();
}

// ------------------------------------------------------------------ iş izleyici
const monitors = new Map();
export function stopMonitor(el) { const m = monitors.get(el); if (m) { m.stop = true; monitors.delete(el); } }

/**
 * Bir arka plan işini canlı izler.
 * opts: {onUpdate(job), onDone(job), live: 'cfd'|'opt'|null}
 */
export function mountJob(el, jobId, opts = {}) {
  stopMonitor(el);
  el.innerHTML = `<div class="card">
    <div class="job-head"><div><b data-j="title">İş</b> <span class="badge" data-j="status"></span>
      <div class="small muted" data-j="stage"></div></div>
      <div class="actions"><span class="small muted" data-j="elapsed"></span>
      <button class="btn sm danger" data-j="cancel">${icon('stop')} Durdur</button></div></div>
    <div class="progress"><div data-j="bar" style="width:0%"></div></div>
    <div data-j="live" class="${opts.live ? '' : 'hidden'}" style="margin-top:14px"></div>
    <details class="adv" data-j="logwrap" ${opts.openLog ? 'open' : ''}><summary>Günlük</summary><div class="console" data-j="log"></div></details>
  </div>`;
  const q = (k) => el.querySelector(`[data-j="${k}"]`);
  const m = { stop: false, since: 0 };
  monitors.set(el, m);
  q('cancel').onclick = async () => { await api.post(`/api/jobs/${jobId}/cancel`); toast('İptal isteği gönderildi'); };
  if (opts.live === 'cfd') {
    q('live').innerHTML = `<div class="lbl">Artıklar (residuals)</div><div class="chart short" data-j="res"></div>
      <div class="grid g2" style="margin-top:8px"><div><div class="lbl">CL yakınsaması</div><div class="chart short" data-j="coef"></div></div>
      <div><div class="lbl">CD yakınsaması</div><div class="chart short" data-j="coefd"></div></div></div>
      <div data-j="partial" style="margin-top:10px"></div>`;
  }
  const STATUS = { queued: ['Kuyrukta', ''], running: ['Çalışıyor', 'info'], done: ['Tamamlandı', 'good'], failed: ['Hata', 'bad'], cancelled: ['İptal', 'warn'] };
  const tick = async () => {
    if (m.stop) return;
    let j;
    try { j = await api.get(`/api/jobs/${jobId}?since=${m.since}`); } catch (e) {
      q('stage').textContent = 'İş bulunamadı (sunucu yeniden başlatılmış olabilir).';
      q('cancel').classList.add('hidden');
      monitors.delete(el);
      opts.onLost?.();
      return;
    }
    q('title').textContent = j.title;
    const [st, cls] = STATUS[j.status] || [j.status, ''];
    q('status').textContent = st; q('status').className = `badge ${cls}`;
    q('stage').textContent = j.error ? j.error.split('\n')[0] : j.stage;
    q('bar').style.width = `${(j.progress * 100).toFixed(1)}%`;
    q('elapsed').textContent = j.started ? fmtTime(j.elapsed) + (j.status === 'running' && j.progress > 0.03 ? ` · kalan ~${fmtTime(j.elapsed * (1 - j.progress) / j.progress)}` : '') : '';
    if (j.logs?.length) {
      const c = q('log');
      const atBottom = c.scrollTop + c.clientHeight >= c.scrollHeight - 30;
      c.insertAdjacentHTML('beforeend', j.logs.map(([, s]) => `<div class="${s.startsWith('▶') ? 'stage' : (/HATA|Error|FATAL/i.test(s) ? 'err' : '')}">${esc(s)}</div>`).join(''));
      m.since = j.last_log;
      if (atBottom) c.scrollTop = c.scrollHeight;
    }
    if (opts.live === 'cfd') renderCfdLive(q, j);
    opts.onUpdate?.(j);
    if (['done', 'failed', 'cancelled'].includes(j.status)) {
      q('cancel').classList.add('hidden');
      if (j.status === 'failed') { q('logwrap').open = true; }
      monitors.delete(el);
      opts.onDone?.(j);
      return;
    }
    setTimeout(tick, 1500);
  };
  tick();
}

function renderCfdLive(q, j) {
  const live = j.data?.live;
  if (live?.residuals?.iter?.length) {
    const r = live.residuals;
    const keys = Object.keys(r).filter((k) => k !== 'iter');
    const order = ['p', 'Ux', 'Uy', 'Uz', 'k', 'omega', 'epsilon', 'nuTilda'];
    keys.sort((a, b) => order.indexOf(a) - order.indexOf(b));
    plot(q('res'), keys.map((k, i) => ({ x: r.iter, y: r[k], name: k, type: 'scatter', mode: 'lines', line: { width: 1.6, color: SERIES()[i % 8] } })),
      { yaxis: { type: 'log', title: 'başlangıç artığı', exponentformat: 'e' }, xaxis: { title: 'iterasyon' }, margin: { l: 60, r: 70, t: 12, b: 40 },
        legend: { orientation: 'v', x: 1.01, y: 1, xanchor: 'left' } });
  }
  if (live?.coeffs?.iter?.length) {
    const c = live.coeffs;
    const mini = { xaxis: { title: 'iterasyon' }, margin: { l: 56, r: 10, t: 24, b: 40 }, showlegend: false };
    plot(q('coef'), [{ x: c.iter, y: c.CL, name: 'CL', type: 'scatter', mode: 'lines', line: { width: 2, color: SERIES()[0] } }], { ...mini, yaxis: { title: 'CL' } });
    plot(q('coefd'), [{ x: c.iter, y: c.CD, name: 'CD', type: 'scatter', mode: 'lines', line: { width: 2, color: SERIES()[1] } }], { ...mini, yaxis: { title: 'CD' } });
  } else if (live && live.iteration) {
    q('coef').innerHTML = empty('Bu OpenFOAM kurulumunda katsayı geçmişi yazılmıyor; sonuçlar çözüm sonunda alanlardan hesaplanır.');
    q('coefd').innerHTML = '';
  }
  const res = j.data?.results || j.result?.results;
  if (res?.length) q('partial').innerHTML = `<div class="lbl" style="margin-bottom:6px">Tamamlanan açılar</div>` + resultsTable(res);
}

// ------------------------------------------------------------------ ortam / tema
export function applyTheme(theme) {
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}

export async function refreshEnv() {
  try {
    const s = await api.get('/api/setup/status');
    state.env = s;
    const pill = $('#env-pill');
    const label = s.runner === 'docker' ? 'Docker · OpenFOAM hazır' : s.runner === 'local' ? 'Yerel OpenFOAM hazır' : 'CFD için kurulum gerekli';
    const ok = s.ready;
    pill.className = `env-pill ${ok ? 'ok' : 'warn'}`;
    pill.innerHTML = `<span class="dot"></span><span>${label}</span>`;
    return s;
  } catch (e) { return null; }
}

