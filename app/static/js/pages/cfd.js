import { $, $$, api, state, saveState, toast, fmt, esc, icon, mountJob, stopMonitor, polarCharts, resultsTable, empty, refreshEnv } from '../core.js';
import { numField, bindNumFields } from './design.js';
import { mountFlowViz } from '../flowviz.js';

let adv = null;

export async function render(main) {
  const meta = state.meta;
  const settings = meta.settings;
  adv = adv || { preset: 'orta', n_procs: settings.n_procs, layers: { enabled: false, n: 3, expansion: 1.2, final_thickness: 0.4 } };
  const ext = state.external;
  main.innerHTML = `
  <div class="page-head">
    <div><h1>3B CFD Analizi</h1><p>OpenFOAM ile sıkıştırılamaz RANS (k-ω SST). Ağ otomatik üretilir, her hücum açısı ayrı bir vaka olarak çözülür; kaldırma, sürükleme ve moment katsayıları hesaplanır.</p></div>
  </div>
  <div id="env-alert"></div>
  <div class="split">
    <div class="grid">
      <div class="card"><h2>Geometri kaynağı</h2>
        <div class="opt-cards" id="src">
          <div class="opt-card ${state.cfdSource !== 'external' ? 'on' : ''}" data-s="wing"><input type="radio" name="src" ${state.cfdSource !== 'external' ? 'checked' : ''}>
            <div><b>Tasarlanan kanat</b><span>${esc(meta.wing_types[state.cfg.wing.type]?.label)} · yarım model (simetri düzlemi)</span></div></div>
          <div class="opt-card ${state.cfdSource === 'external' ? 'on' : ''}" data-s="external"><input type="radio" name="src" ${state.cfdSource === 'external' ? 'checked' : ''}>
            <div><b>Harici geometri</b><span>${ext ? `${esc(ext.name)} · S = ${fmt(ext.aref)} m² · ${ext.transform?.symmetric ? 'yarım model' : 'tam model'}` : 'Henüz hazırlanmadı – Harici Geometri sayfasından yükleyin'}</span></div></div>
        </div></div>
      <div class="card"><h2>Hücum açıları</h2>
        <div class="field"><label>α listesi [°] <span class="muted">virgülle ayırın</span></label><input type="text" id="alphas" value="${esc(adv.alphas || '0, 4, 8, 12, 16')}"></div>
        <div class="actions">${['4, 8, 12', '0, 4, 8, 12, 16', '0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20', '8'].map((a) => `<button class="btn sm" data-a="${a}">${a.split(',').length} açı</button>`).join('')}</div>
      </div>
      <div class="card"><h2>Ağ ve çözücü</h2>
        <div class="opt-cards" id="presets">${Object.entries(meta.mesh_presets).map(([k, p]) => `<div class="opt-card ${adv.preset === k ? 'on' : ''}" data-p="${k}"><input type="radio" name="preset" ${adv.preset === k ? 'checked' : ''}><div><b>${k[0].toUpperCase() + k.slice(1)}</b><span>${esc(p.label)} · ${p.iterations} iterasyon</span></div></div>`).join('')}</div>
        <div class="field" style="margin-top:12px"><label>İşlemci çekirdeği (paralel)</label><input type="number" id="nprocs" min="1" max="64" value="${adv.n_procs}"></div>
        <details class="adv"><summary>Gelişmiş ağ ayarları</summary><div id="adv" style="margin-top:10px"></div></details>
      </div>
      <div class="card"><h2>Çalıştır</h2>
        <div class="field"><label>Çalışma adı</label><input type="text" id="name" value="${esc(adv.name || 'CFD analizi')}"></div>
        <div class="small muted" id="estimate" style="margin-bottom:10px"></div>
        <button class="btn primary" id="start" style="width:100%">${icon('play')} CFD analizini başlat</button>
      </div>
    </div>
    <div class="grid">
      <div id="job"></div>
      <div id="result">${empty('Analiz başlatıldığında ağ üretimi, artıklar ve katsayı yakınsaması burada canlı izlenir.', 'cfd')}</div>
    </div>
  </div>`;

  const env = state.env || await refreshEnv();
  if (env && !env.ready) {
    $('#env-alert').innerHTML = `<div class="alert warn" style="margin-bottom:16px">⚠︎ OpenFOAM henüz hazır değil. <a href="#/kurulum" style="color:inherit;font-weight:600;margin-left:4px">Kurulum sayfasından</a>&nbsp;Docker + OpenFOAM’u tek tıkla kurabilirsiniz.</div>`;
  } else if (env) {
    $('#env-alert').innerHTML = `<div class="small muted" style="margin:-8px 0 12px">Çalıştırıcı: <b>${env.runner === 'docker' ? `Docker (${esc(env.image.name)})` : 'Yerel OpenFOAM'}</b></div>`;
  }

  $('#src').onclick = (e) => {
    const c = e.target.closest('.opt-card'); if (!c) return;
    if (c.dataset.s === 'external' && !state.external) { toast('Önce Harici Geometri sayfasında bir model yükleyip hazırlayın'); location.hash = '#/harici'; return; }
    state.cfdSource = c.dataset.s; saveState();
    $$('#src .opt-card').forEach((x) => { x.classList.toggle('on', x === c); x.querySelector('input').checked = x === c; });
    estimate();
  };
  $$('[data-a]').forEach((b) => { b.onclick = () => { $('#alphas').value = b.dataset.a; adv.alphas = b.dataset.a; estimate(); }; });
  $('#alphas').oninput = (e) => { adv.alphas = e.target.value; estimate(); };
  $('#name').oninput = (e) => { adv.name = e.target.value; };
  $('#nprocs').oninput = (e) => { adv.n_procs = +e.target.value; estimate(); };
  $('#presets').onclick = (e) => {
    const c = e.target.closest('.opt-card'); if (!c) return;
    adv.preset = c.dataset.p;
    for (const k of ['base_cell_size', 'surface_level', 'feature_level', 'near_level', 'wake_level', 'iterations']) delete adv[k];
    $$('#presets .opt-card').forEach((x) => { x.classList.toggle('on', x === c); x.querySelector('input').checked = x === c; });
    renderAdv(); estimate();
  };

  function renderAdv() {
    const p = meta.mesh_presets[adv.preset];
    const v = (k) => adv[k] ?? p[k];
    const sl = v('surface_level');
    $('#adv').innerHTML = numField({ key: 'base_cell_size', label: 'Arka plan hücre boyu', unit: '× ref. uzunluk', min: 0.1, max: 1, step: 0.05 }, v('base_cell_size'))
      + `<div class="row">${numField({ key: 'sl0', label: 'Yüzey seviyesi min', min: 2, max: 8, step: 1, norange: true }, sl[0])}${numField({ key: 'sl1', label: 'Yüzey seviyesi max', min: 2, max: 9, step: 1, norange: true }, sl[1])}</div>`
      + `<div class="row">${numField({ key: 'feature_level', label: 'Kenar seviyesi', min: 2, max: 9, step: 1, norange: true }, v('feature_level'))}${numField({ key: 'near_level', label: 'Yakın bölge', min: 1, max: 6, step: 1, norange: true }, v('near_level'))}${numField({ key: 'wake_level', label: 'İz bölgesi', min: 0, max: 6, step: 1, norange: true }, v('wake_level'))}</div>`
      + numField({ key: 'iterations', label: 'Maksimum iterasyon', min: 100, max: 5000, step: 100 }, v('iterations'))
      + `<label class="check"><input type="checkbox" id="layers" ${adv.layers.enabled ? 'checked' : ''}> Sınır tabakası (prizma katmanları)</label>
         <div id="lay" class="${adv.layers.enabled ? '' : 'hidden'}"><div class="row">${numField({ key: 'ln', label: 'Katman sayısı', min: 1, max: 15, step: 1, norange: true }, adv.layers.n)}${numField({ key: 'le', label: 'Büyüme oranı', min: 1.05, max: 1.5, step: 0.05, norange: true }, adv.layers.expansion)}${numField({ key: 'lf', label: 'Son katman (rel.)', min: 0.1, max: 0.9, step: 0.05, norange: true }, adv.layers.final_thickness)}</div></div>
         <div class="help small muted">Seviye n, hücre boyunun 2ⁿ'e bölünmesi demektir. Her seviye hücre sayısını yüzey çevresinde ~4 kat artırır.</div>`;
    bindNumFields($('#adv'), (k, val) => {
      if (k === 'sl0' || k === 'sl1') { const s = [...(adv.surface_level ?? p.surface_level)]; s[k === 'sl0' ? 0 : 1] = val; adv.surface_level = s; }
      else if (k === 'ln') adv.layers.n = val; else if (k === 'le') adv.layers.expansion = val; else if (k === 'lf') adv.layers.final_thickness = val;
      else adv[k] = val;
      estimate();
    });
    $('#layers').onchange = (e) => { adv.layers.enabled = e.target.checked; $('#lay').classList.toggle('hidden', !e.target.checked); };
  }

  function parseAlphas() { return $('#alphas').value.split(/[,; ]+/).filter(Boolean).map(Number).filter((x) => isFinite(x)); }
  function estimate() {
    const p = meta.mesh_presets[adv.preset];
    const n = parseAlphas().length;
    const per = { kaba: 1.5, orta: 6, ince: 40 }[adv.preset] * 4 / Math.max(1, adv.n_procs);
    $('#estimate').innerHTML = `${n} vaka · tahmini süre ≈ <b>${Math.round(per * n)} dk</b> (${adv.n_procs} çekirdek, ${esc(p.label.split('(')[1]?.replace(')', '') || '')}). Gerçek süre bilgisayara ve geometriye göre değişir.`;
  }

  $('#start').onclick = async () => {
    const alphas = parseAlphas();
    if (!alphas.length) { toast('En az bir hücum açısı girin', 'bad'); return; }
    const mesh = { preset: adv.preset, n_procs: adv.n_procs, layers: adv.layers };
    for (const k of ['base_cell_size', 'surface_level', 'feature_level', 'near_level', 'wake_level', 'iterations']) if (adv[k] !== undefined) mesh[k] = adv[k];
    const body = { config: state.cfg, alphas, mesh, name: $('#name').value };
    if (state.cfdSource === 'external' && state.external) {
      const e = state.external;
      body.external = { id: e.id, transform: e.transform, aref: e.aref, lref: e.lref, cofr: e.cofr };
    }
    try {
      const r = await api.post('/api/cfd/start', body);
      state.activeJobs.cfd = r.job_id; saveState();
      watch(r.job_id);
      toast('CFD analizi kuyruğa alındı', 'good');
    } catch (e) { toast(e.message, 'bad'); }
  };

  renderAdv(); estimate();
  if (state.activeJobs.cfd) watch(state.activeJobs.cfd);
}

function watch(id) {
  $('#result').innerHTML = '';
  mountJob($('#job'), id, {
    live: 'cfd',
    onDone: async (j) => {
      if (j.status === 'done' && j.result?.study_id) showStudy(j.result.study_id);
      else if (j.meta?.study_id && j.status !== 'done') $('#result').innerHTML = `<div class="small muted">Kısmi sonuçlar <a href="#/sonuclar">Sonuçlar</a> sayfasında.</div>`;
    },
    onLost: () => { delete state.activeJobs.cfd; saveState(); },
  });
}

export async function showStudy(sid, el = $('#result')) {
  const s = await api.get(`/api/studies/${sid}`);
  const series = [{ name: 'OpenFOAM RANS', rows: s.results }];
  if (s.comparison) series.push({ name: 'VLM + Polhamus', rows: s.comparison, dash: true });
  el.innerHTML = `<div class="card"><h2>${esc(s.name)} <span class="actions">
      <a class="btn sm" href="/api/studies/${sid}/report" target="_blank">${icon('report')} Rapor</a>
      <a class="btn sm" href="/api/studies/${sid}/download">${icon('download')} ZIP</a>
      <a class="btn sm" href="/api/studies/${sid}/file/geometry.stl">${icon('download')} STL</a>
      <button class="btn sm" data-open>${icon('folder')} Klasör</button></span></h2>
      ${resultsTable(s.results, [['drag_pressure_N', 'Basınç sürük. [N]'], ['drag_viscous_N', 'Sürtünme sürük. [N]'], ['cells', 'Hücre'], ['converged_hint', 'Yakınsama']])}
      <p class="small muted" style="margin:10px 0 0">Kuvvetler tam ${s.kind === 'external' ? 'gövde' : 'kanat'} içindir. q = ${fmt(s.results[0]?.q_Pa)} Pa, S = ${fmt(s.results[0]?.area_m2)} m². Kesin değerler için ağı inceltip sonuçların değişmediğini doğrulayın.</p>
    </div>
    <div id="fv" style="margin-top:16px"></div>
    ${s.results.length > 1 ? '<div id="sp" style="margin-top:16px"></div>' : ''}`;
  el.querySelector('[data-open]').onclick = async () => { const r = await api.post(`/api/studies/${sid}/open_folder`); toast(r.path); };
  if (s.results.length > 1) polarCharts(el.querySelector('#sp'), series);
  mountFlowViz(el.querySelector('#fv'), sid);
}

export function leave() { stopMonitor($('#job')); }
