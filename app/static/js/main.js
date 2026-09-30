import { $, api, state, loadState, saveState, icon, toast, clone, applyTheme, refreshEnv } from './core.js';
import * as dashboard from './pages/dashboard.js';
import * as design from './pages/design.js';
import * as quick from './pages/quick.js';
import * as cfd from './pages/cfd.js';
import * as external from './pages/external.js';
import * as optimize from './pages/optimize.js';
import * as results from './pages/results.js';
import * as flow from './pages/flow.js';
import * as setup from './pages/setup.js';
import * as settings from './pages/settings.js';

const ROUTES = [
  ['panel', 'Panel', 'dashboard', dashboard],
  ['tasarim', 'Kanat Tasarımı', 'design', design],
  ['hizli', 'Hızlı Analiz', 'quick', quick],
  ['cfd', '3B CFD Analizi', 'cfd', cfd],
  ['harici', 'Harici Geometri', 'external', external],
  ['akis', 'Akış Görselleştirme', 'flow', flow],
  ['optimizasyon', 'Optimizasyon', 'optimize', optimize],
  ['sonuclar', 'Sonuçlar', 'results', results],
  null,
  ['kurulum', 'Kurulum', 'setup', setup],
  ['ayarlar', 'Ayarlar', 'settings', settings],
];

let current = null;

function renderNav(active) {
  $('#nav').innerHTML = ROUTES.map((r) => r ? `<a href="#/${r[0]}" class="${r[0] === active ? 'active' : ''}">${icon(r[2])}<span>${r[1]}</span></a>` : '<div class="sep"></div>').join('');
}

async function route() {
  const key = (location.hash.replace(/^#\/?/, '').split('?')[0]) || 'panel';
  const r = ROUTES.find((x) => x && x[0] === key) || ROUTES[0];
  if (current?.leave) current.leave();
  current = r[3];
  renderNav(r[0]);
  const main = $('#main');
  main.scrollTop = 0;
  main.innerHTML = '';
  try { await current.render(main); } catch (e) { console.error(e); main.innerHTML = `<div class="alert bad">Sayfa yüklenemedi: ${e.message}</div>`; }
}

async function pollJobs() {
  try {
    const list = await api.get('/api/jobs');
    const running = list.filter((j) => j.status === 'running' || j.status === 'queued');
    const pill = $('#jobs-pill');
    if (running.length) {
      pill.classList.remove('hidden');
      const j = running[0];
      pill.innerHTML = `<span class="spinner"></span><span>${running.length} iş çalışıyor · ${Math.round(j.progress * 100)}%</span>`;
    } else pill.classList.add('hidden');
    for (const j of list) {
      if (pollJobs.seen?.[j.id] && pollJobs.seen[j.id] !== j.status && ['done', 'failed'].includes(j.status)) {
        toast(`${j.title}: ${j.status === 'done' ? 'tamamlandı' : 'hata'}`, j.status === 'done' ? 'good' : 'bad');
      }
    }
    pollJobs.seen = Object.fromEntries(list.map((j) => [j.id, j.status]));
  } catch (e) { /* sunucu kapalı */ }
  setTimeout(pollJobs, 3000);
}

async function init() {
  loadState();
  state.meta = await api.get('/api/meta');
  applyTheme(state.meta.settings.theme);
  if (!state.cfg) {
    const d = clone(state.meta.defaults);
    state.cfg = { wing: clone(state.meta.type_defaults.delta), airfoil: d.airfoil, flow: { ...d.flow, altitude_m: 0 }, geometry: d.geometry, vlm: d.vlm };
    state.cfg.airfoil.tip = { type: 'naca4', code: '0006' };
    saveState();
  }
  $('#env-pill').onclick = () => { location.hash = '#/kurulum'; };
  $('#jobs-pill').onclick = () => { location.hash = '#/panel'; };
  window.addEventListener('hashchange', route);
  route();
  refreshEnv();
  pollJobs();
}

init();
