import { $, $$, api, state, saveState, toast, fmt, esc, icon, plot, SERIES, css, debounce, modal, closeModal, clone, fmtDate } from '../core.js';

export const TYPE_ICONS = {
  delta: 'M30 4 L56 36 L4 36 Z',
  cropped_delta: 'M30 4 L50 27 L50 36 L10 36 L10 27 Z',
  double_delta: 'M30 3 L35 17 L56 33 L56 37 L4 37 L4 33 L25 17 Z',
  trapezoidal: 'M30 6 L56 22 L56 29 L30 23 L4 29 L4 22 Z',
  rectangular: 'M4 14 H56 V26 H4 Z',
  elliptical: 'M4 20 C4 11 56 11 56 20 C56 29 4 29 4 20 Z',
  custom: 'M30 4 L42 16 L56 30 L56 34 L4 34 L4 30 L18 16 Z',
};

let previewSeq = 0;

export async function render(main) {
  const meta = state.meta;
  main.innerHTML = `
  <div class="page-head">
    <div><h1>Kanat Tasarımı</h1><p>Planform tipini seçin, geometri ve profil parametrelerini ayarlayın. Önizleme ve geometrik büyüklükler anında güncellenir.</p></div>
    <div class="actions">
      <button class="btn" id="lib">${icon('folder')} Tasarım kütüphanesi</button>
      <button class="btn" id="save">${icon('save')} Kaydet</button>
      <button class="btn" id="stl">${icon('download')} STL indir</button>
      <a class="btn" href="#/hizli">${icon('quick')} Hızlı analiz</a>
      <a class="btn primary" href="#/cfd" id="to-cfd">${icon('cfd')} CFD analizi</a>
    </div>
  </div>
  <div class="split-wide">
    <div class="grid">
      <div class="card"><h2>Kanat tipi</h2><div class="types" id="types"></div><p class="small muted" id="type-desc" style="margin:10px 0 0"></p></div>
      <div class="card"><h2>Planform</h2><div id="params"></div></div>
      <div class="card"><h2>Kanat profili (airfoil)</h2>
        <div class="tabs" id="af-tabs"><button data-t="root" class="on">Kök</button><button data-t="tip">Uç</button></div>
        <div id="af-editor"></div>
        <div class="field" style="margin-top:8px"><label>Kök → uç karışım üssü <span class="muted">1 = doğrusal</span></label>
          <div class="rng"><input type="range" min="0.2" max="4" step="0.1" id="blend-r"><input type="number" min="0.2" max="4" step="0.1" id="blend"></div></div>
      </div>
      <div class="card"><h2>Akış koşulları</h2><div id="flow"></div></div>
    </div>
    <div class="grid sticky">
      <div class="card">
        <div class="tabs" id="view-tabs"><button data-v="3d" class="on">3B görünüm</button><button data-v="plan">Planform</button><button data-v="sec">Kesitler</button></div>
        <div id="view" class="chart xl"></div>
      </div>
      <div id="warn"></div>
      <div class="stats" id="stats"></div>
    </div>
  </div>`;

  // --- tip seçici
  const types = $('#types');
  types.innerHTML = Object.entries(meta.wing_types).map(([k, t]) =>
    `<div class="type ${state.cfg.wing.type === k ? 'on' : ''}" data-k="${k}"><svg viewBox="0 0 60 40"><path d="${TYPE_ICONS[k]}"/></svg>${esc(t.label)}</div>`).join('');
  types.onclick = (e) => {
    const el = e.target.closest('.type'); if (!el) return;
    const k = el.dataset.k;
    if (k === state.cfg.wing.type) return;
    const keep = { twist_tip_deg: state.cfg.wing.twist_tip_deg ?? 0, dihedral_deg: state.cfg.wing.dihedral_deg ?? 0 };
    state.cfg.wing = { ...clone(meta.type_defaults[k]), ...keep };
    $$('.type', types).forEach((x) => x.classList.toggle('on', x.dataset.k === k));
    renderParams(); update();
  };

  let afTab = 'root';
  $('#af-tabs').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; afTab = b.dataset.t; $$('#af-tabs button').forEach((x) => x.classList.toggle('on', x === b)); renderAirfoil(afTab); };
  const bl = state.cfg.airfoil.blend_exponent ?? 1;
  $('#blend').value = bl; $('#blend-r').value = bl;
  const onBlend = (v) => { state.cfg.airfoil.blend_exponent = +v; $('#blend').value = v; $('#blend-r').value = v; update(); };
  $('#blend').oninput = (e) => onBlend(e.target.value); $('#blend-r').oninput = (e) => onBlend(e.target.value);

  let view = '3d';
  $('#view-tabs').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; view = b.dataset.v; $$('#view-tabs button').forEach((x) => x.classList.toggle('on', x === b)); drawView(); };

  let last = null;
  const update = debounce(async () => {
    saveState();
    const seq = ++previewSeq;
    try {
      const r = await api.post('/api/geometry/preview', state.cfg);
      if (seq !== previewSeq) return;
      last = r;
      renderStats(r);
      $('#warn').innerHTML = r.warnings.map((w) => `<div class="alert warn">⚠︎ ${esc(w)}</div>`).join('');
      drawView();
    } catch (e) {
      $('#warn').innerHTML = `<div class="alert bad">${esc(e.message)}</div>`;
    }
  }, 250);

  function drawView() {
    if (!last) return;
    const el = $('#view');
    if (view === '3d') draw3D(el, last);
    else if (view === 'plan') drawPlan(el, last);
    else drawSections(el, last);
  }

  function renderParams() {
    const t = meta.wing_types[state.cfg.wing.type];
    $('#type-desc').textContent = t.description;
    const w = state.cfg.wing;
    let html = t.params.map((p) => numField(p, w[p.key] ?? p.default)).join('');
    if (w.type === 'custom') html = sectionTable(w.sections) + html;
    $('#params').innerHTML = html;
    bindNumFields($('#params'), (k, v) => { state.cfg.wing[k] = v; update(); });
    if (w.type === 'custom') bindSectionTable();
  }

  function sectionTable(secs) {
    return `<div class="lbl" style="margin-bottom:6px">Yarı kanat kesitleri <span class="muted">m, °</span></div>
      <div class="tbl-wrap" style="margin-bottom:8px"><table class="tbl" id="sec-tbl"><thead><tr><th>y</th><th>Hücum kenarı x</th><th>Veter</th><th>Burulma</th><th></th></tr></thead><tbody>
      ${secs.map((s, i) => `<tr>${[0, 1, 2, 3].map((j) => `<td><input type="number" step="0.01" data-i="${i}" data-j="${j}" value="${s[j] ?? 0}"></td>`).join('')}
        <td><button class="btn sm ghost danger" data-del="${i}" ${secs.length <= 2 ? 'disabled' : ''}>✕</button></td></tr>`).join('')}
      </tbody></table></div><button class="btn sm" id="sec-add">${icon('plus')} Kesit ekle</button><div style="height:12px"></div>`;
  }
  function bindSectionTable() {
    const secs = state.cfg.wing.sections;
    $('#sec-tbl').oninput = (e) => { const i = +e.target.dataset.i, j = +e.target.dataset.j; if (isNaN(i)) return; secs[i][j] = +e.target.value; update(); };
    $('#sec-tbl').onclick = (e) => { const d = e.target.closest('[data-del]'); if (!d) return; secs.splice(+d.dataset.del, 1); renderParams(); update(); };
    $('#sec-add').onclick = () => { const l = secs[secs.length - 1]; secs.push([+(l[0] + 0.2).toFixed(3), +(l[1] + 0.15).toFixed(3), +(l[2] * 0.7).toFixed(3), l[3]]); renderParams(); update(); };
  }

  function renderAirfoil(which) {
    const spec = state.cfg.airfoil[which] || { type: 'naca4', code: '0008' };
    state.cfg.airfoil[which] = spec;
    const presets = meta.airfoil_presets;
    const mode = spec.type;
    const naca = spec.type === 'naca4' ? nacaParams(spec) : { m: 0, p: 0.4, t: 0.08 };
    $('#af-editor').innerHTML = `
      <div class="field"><label>Hazır profil</label>
        <select id="af-preset"><option value="">— seçin —</option>${presets.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join('')}</select></div>
      <div class="field"><label>Parametrizasyon</label>
        <div class="seg" id="af-mode">${[['naca4', 'NACA 4'], ['cst', 'CST'], ['file', 'Dosya']].map(([k, l]) => `<button data-m="${k}" class="${mode === k ? 'on' : ''}">${l}</button>`).join('')}</div></div>
      <div id="af-fields"></div>
      <div class="chart short" id="af-plot" style="height:150px"></div>`;
    const fields = $('#af-fields');
    if (mode === 'naca4') {
      fields.innerHTML = [
        numField({ key: 'm', label: 'Maksimum kamber', unit: '% veter', min: 0, max: 9, step: 0.1 }, +(naca.m * 100).toFixed(2)),
        numField({ key: 'p', label: 'Kamber konumu', unit: '% veter', min: 10, max: 90, step: 1 }, +(naca.p * 100).toFixed(1)),
        numField({ key: 't', label: 'Maksimum kalınlık', unit: '% veter', min: 1, max: 30, step: 0.1 }, +(naca.t * 100).toFixed(2)),
      ].join('') + `<div class="small muted" id="naca-code"></div>`;
      const code = () => { const n = nacaParams(state.cfg.airfoil[which]); $('#naca-code').textContent = `Eşdeğer: NACA ${Math.round(n.m * 100)}${Math.round(n.p * 10)}${String(Math.round(n.t * 100)).padStart(2, '0')}`; };
      code();
      bindNumFields(fields, (k, v) => {
        const cur = nacaParams(state.cfg.airfoil[which]);
        const nx = { m: cur.m, p: cur.p, t: cur.t, [k]: v / 100 };
        state.cfg.airfoil[which] = { type: 'naca4', camber: nx.m, camber_pos: nx.p, thickness: nx.t };
        code(); afPlot(which); update();
      });
    } else if (mode === 'cst') {
      const up = spec.upper || [0.15, 0.17, 0.16, 0.14], lo = spec.lower || [-0.15, -0.17, -0.16, -0.14];
      fields.innerHTML = `<div class="field"><label>Üst yüzey ağırlıkları</label><input type="text" id="cst-u" value="${up.join(', ')}"></div>
        <div class="field"><label>Alt yüzey ağırlıkları <span class="muted">genelde negatif</span></label><input type="text" id="cst-l" value="${lo.join(', ')}"></div>
        <div class="field"><label>Firar kenarı kalınlığı</label><input type="number" id="cst-te" step="0.001" value="${spec.te_thickness ?? 0}"></div>
        <div class="help small muted">Kulfan CST: her liste Bernstein polinom katsayılarıdır; optimizasyonda <span class="mono">airfoil.root.upper.0</span> gibi değişkenlerle kullanılabilir.</div>`;
      const onCst = () => {
        const parse = (s) => s.split(/[,; ]+/).filter(Boolean).map(Number);
        state.cfg.airfoil[which] = { type: 'cst', upper: parse($('#cst-u').value), lower: parse($('#cst-l').value), te_thickness: +$('#cst-te').value || 0 };
        afPlot(which); update();
      };
      ['#cst-u', '#cst-l', '#cst-te'].forEach((s) => { $(s).oninput = debounce(onCst, 400); });
      if (spec.type !== 'cst') onCst();
    } else {
      const files = presets.filter((p) => p.spec.type === 'file');
      fields.innerHTML = `<div class="field"><label>Yüklenmiş profil</label><select id="af-file">${files.length ? files.map((p) => `<option value="${esc(p.spec.path)}" ${spec.path === p.spec.path ? 'selected' : ''}>${esc(p.name)}</option>`).join('') : '<option value="">(henüz yok)</option>'}</select></div>
        <label class="btn sm">${icon('upload')} .dat yükle (Selig formatı)<input type="file" id="af-up" accept=".dat,.txt" hidden></label>`;
      $('#af-file').onchange = (e) => { if (e.target.value) { state.cfg.airfoil[which] = { type: 'file', path: e.target.value }; afPlot(which); update(); } };
      $('#af-up').onchange = async (e) => {
        const f = e.target.files[0]; if (!f) return;
        try {
          const r = await api.upload('/api/airfoil/upload', f);
          state.meta = await api.get('/api/meta');
          state.cfg.airfoil[which] = r.spec; renderAirfoil(which); update(); toast('Profil yüklendi', 'good');
        } catch (err) { toast(err.message, 'bad'); }
      };
      if (spec.type === 'file') afPlot(which);
      else if (files.length) { state.cfg.airfoil[which] = { type: 'file', path: files[0].spec.path }; afPlot(which); update(); }
    }
    $('#af-mode').onclick = (e) => {
      const b = e.target.closest('button'); if (!b) return;
      const m = b.dataset.m;
      if (m === 'naca4') state.cfg.airfoil[which] = { type: 'naca4', camber: 0, camber_pos: 0.4, thickness: 0.08 };
      else if (m === 'cst') state.cfg.airfoil[which] = { type: 'cst', upper: [0.15, 0.17, 0.16, 0.14], lower: [-0.15, -0.17, -0.16, -0.14], te_thickness: 0 };
      else state.cfg.airfoil[which] = { type: 'file', path: '' };
      renderAirfoil(which); if (m !== 'file') update();
    };
    $('#af-preset').onchange = (e) => {
      if (e.target.value === '') return;
      state.cfg.airfoil[which] = clone(presets[+e.target.value].spec);
      renderAirfoil(which); update();
    };
    if (mode !== 'file') afPlot(which);
  }

  async function afPlot(which) {
    try {
      const r = await api.post('/api/airfoil/preview', state.cfg.airfoil[which]);
      plot($('#af-plot'), [{ x: r.x, y: r.z, type: 'scatter', mode: 'lines', fill: 'toself', fillcolor: css('--accent-soft'), line: { color: SERIES()[0], width: 2 }, hoverinfo: 'skip' }],
        { margin: { l: 36, r: 8, t: 8, b: 24 }, xaxis: { range: [-0.02, 1.02] }, yaxis: { scaleanchor: 'x', scaleratio: 1 }, showlegend: false,
          annotations: [{ x: 1, y: 1, xref: 'paper', yref: 'paper', xanchor: 'right', showarrow: false, text: `t/c = ${(r.t_c * 100).toFixed(1)}%  ·  kamber = ${(r.camber * 100).toFixed(1)}%`, font: { size: 11, color: css('--text-2') } }] });
    } catch (e) { $('#af-plot').innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; }
  }

  function renderFlow() {
    const f = state.cfg.flow;
    const isa = f.altitude_m !== null && f.altitude_m !== undefined;
    $('#flow').innerHTML = `
      ${numField({ key: 'velocity', label: 'Serbest akış hızı', unit: 'm/s', min: 1, max: 300, step: 0.5 }, f.velocity)}
      <div class="field"><label>Atmosfer</label><div class="seg" id="atm"><button data-a="isa" class="${isa ? 'on' : ''}">ISA irtifa</button><button data-a="man" class="${isa ? '' : 'on'}">Elle</button></div></div>
      <div id="atm-f"></div>`;
    const af = $('#atm-f');
    if (isa) {
      af.innerHTML = numField({ key: 'altitude_m', label: 'İrtifa', unit: 'm', min: 0, max: 20000, step: 100 }, f.altitude_m)
        + numField({ key: 'isa_dT', label: 'ISA sıcaklık sapması', unit: 'K', min: -40, max: 40, step: 1 }, f.isa_dT ?? 0)
        + `<div class="small muted" id="isa-info"></div>`;
    } else {
      af.innerHTML = numField({ key: 'density', label: 'Yoğunluk', unit: 'kg/m³', min: 0.01, max: 2, step: 0.001 }, f.density)
        + numField({ key: 'kinematic_viscosity', label: 'Kinematik viskozite', unit: 'm²/s', min: 1e-6, max: 1e-3, step: 1e-7, norange: true }, f.kinematic_viscosity)
        + numField({ key: 'speed_of_sound', label: 'Ses hızı', unit: 'm/s', min: 200, max: 400, step: 0.1 }, f.speed_of_sound);
    }
    bindNumFields($('#flow'), (k, v) => { state.cfg.flow[k] = v; update(); });
    $('#atm').onclick = (e) => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.a === 'isa') state.cfg.flow.altitude_m = state.cfg.flow.altitude_m ?? 0;
      else { state.cfg.flow.altitude_m = null; }
      renderFlow(); update();
    };
  }

  function renderStats(r) {
    const s = r.summary, f = r.flow;
    const st = (k, v, u = '') => `<div class="stat"><div class="k">${k}</div><div class="v">${v}<span class="u">${u}</span></div></div>`;
    $('#stats').innerHTML = st('Referans alan', fmt(s.area_m2), ' m²') + st('Açıklık oranı', fmt(s.aspect_ratio, 3))
      + st('OAV (MAC)', fmt(s.mac_m), ' m') + st('Ok açısı (HK)', fmt(s.le_sweep_deg, 3), '°')
      + st('İç hacim', fmt(s.volume_m3 * 1000), ' L') + st('Reynolds', fmt(f.reynolds / 1e6, 3), ' ×10⁶')
      + st('Mach', fmt(f.mach, 3)) + st('Dinamik basınç', fmt(f.dynamic_pressure_Pa), ' Pa');
    if ($('#isa-info')) $('#isa-info').textContent = `ρ = ${fmt(f.density)} kg/m³ · ν = ${fmt(f.kinematic_viscosity)} m²/s · a = ${fmt(f.speed_of_sound)} m/s`;
  }

  $('#save').onclick = async () => {
    const name = prompt('Tasarım adı:', `${meta.wing_types[state.cfg.wing.type].label} tasarımı`);
    if (!name) return;
    try { await api.post(`/api/designs/${encodeURIComponent(name)}`, state.cfg); toast('Tasarım kaydedildi', 'good'); } catch (e) { toast(e.message, 'bad'); }
  };
  $('#lib').onclick = () => openLibrary(() => render(main));
  $('#stl').onclick = async () => {
    const r = await fetch('/api/geometry/stl', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.cfg) });
    if (!r.ok) { toast('STL oluşturulamadı', 'bad'); return; }
    const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob()); a.download = `${state.cfg.wing.type}_kanat.stl`; a.click();
  };
  $('#to-cfd').onclick = () => { state.cfdSource = 'wing'; saveState(); };

  renderParams(); renderAirfoil('root'); renderFlow(); update();
}

// ------------------------------------------------------------------ yardımcılar

export function nacaParams(spec) {
  let m = 0, p = 0.4, t = 0.08;
  if (spec.code) { const c = String(spec.code).replace(/\D/g, '').padStart(4, '0'); m = +c[0] / 100; p = +c[1] / 10 || 0.4; t = +c.slice(2) / 100; }
  if (spec.camber !== undefined) m = +spec.camber;
  if (spec.camber_pos !== undefined) p = +spec.camber_pos;
  if (spec.thickness !== undefined) t = +spec.thickness;
  return { m, p, t };
}

export function numField(p, value) {
  const range = !p.norange && p.min !== undefined && p.max !== undefined;
  const help = p.help ? `<div class="help">${esc(p.help)}</div>` : '';
  const unit = p.unit && p.unit !== '-' ? `<span class="muted">${esc(p.unit)}</span>` : '';
  const num = `<input type="number" data-k="${p.key}" ${p.min !== undefined ? `min="${p.min}"` : ''} ${p.max !== undefined ? `max="${p.max}"` : ''} step="${p.step ?? 'any'}" value="${value}">`;
  return `<div class="field"><label>${esc(p.label)} ${unit}</label>${range ? `<div class="rng"><input type="range" data-r="${p.key}" min="${p.min}" max="${p.max}" step="${p.step ?? 'any'}" value="${value}">${num}</div>` : num}${help}</div>`;
}

export function bindNumFields(root, onChange) {
  root.oninput = (e) => {
    const t = e.target;
    const k = t.dataset.k || t.dataset.r;
    if (!k || t.closest('#sec-tbl')) return;
    const v = parseFloat(t.value);
    if (isNaN(v)) return;
    const twin = t.dataset.k ? root.querySelector(`[data-r="${k}"]`) : root.querySelector(`[data-k="${k}"]`);
    if (twin) twin.value = t.value;
    onChange(k, v);
  };
}

export function draw3D(el, r, opts = {}) {
  const m = r.mesh;
  const xs = m.x, ys = m.y, zs = m.z;
  const mm = (a) => { let lo = Infinity, hi = -Infinity; for (const v of a) { if (v < lo) lo = v; if (v > hi) hi = v; } return [lo, hi]; };
  const [minx, maxx] = mm(xs);
  const L = maxx - minx;
  const traces = [{
    type: 'mesh3d', x: xs, y: ys, z: zs, i: m.i, j: m.j, k: m.k, color: css('--s1'), flatshading: false,
    lighting: { ambient: 0.45, diffuse: 0.75, specular: 0.25, roughness: 0.6, fresnel: 0.1 }, lightposition: { x: -100, y: 200, z: 300 },
    hoverinfo: 'skip', name: 'geometri',
  }];
  if (opts.flow !== false) {
    traces.push({ type: 'cone', x: [minx - 0.35 * L], y: [0], z: [0], u: [1], v: [0], w: [0], sizemode: 'absolute', sizeref: 0.18 * L, anchor: 'tip',
      colorscale: [[0, css('--s2')], [1, css('--s2')]], showscale: false, hoverinfo: 'skip', name: 'akış' });
  }
  // gerçek oranlar; ince gövdelerde z ekseni okunabilir kalsın diye alt sınır
  const ext = (a) => { const [lo, hi] = mm(a); return hi - lo; };
  const ex = ext(xs) + (opts.flow !== false ? 0.4 * L : 0), ey = ext(ys), ez = ext(zs);
  const mx = Math.max(ex, ey, ez);
  const aspect = { x: ex / mx, y: ey / mx, z: Math.max(ez / mx, 0.18) };
  if (el.querySelector(':scope > .empty, :scope > .alert')) el.innerHTML = '';
  const grid = css('--grid');
  const ax = (t) => ({ title: { text: t, font: { color: css('--text-2'), size: 11 } }, gridcolor: grid, zerolinecolor: grid, backgroundcolor: 'rgba(0,0,0,0)', showbackground: false, tickfont: { color: css('--text-3'), size: 10 } });
  Plotly.react(el, traces, {
    paper_bgcolor: 'rgba(0,0,0,0)', margin: { l: 0, r: 0, t: 0, b: 0 }, showlegend: false,
    scene: { aspectmode: 'manual', aspectratio: aspect, xaxis: ax('x [m] (akış →)'), yaxis: ax('y [m]'), zaxis: ax('z [m]'),
             camera: { eye: { x: -0.95, y: -1.1, z: 0.62 }, up: { x: 0, y: 0, z: 1 }, center: { x: 0.02, y: 0, z: -0.04 } } },
  }, { displaylogo: false, responsive: true });
}

function drawPlan(el, r) {
  const o = r.outline, mac = r.mac;
  plot(el, [
    { x: o.y, y: o.x, type: 'scatter', mode: 'lines', fill: 'toself', fillcolor: css('--accent-soft'), line: { color: SERIES()[0], width: 2 }, name: 'planform', hovertemplate: 'y = %{x:.3f} m<br>x = %{y:.3f} m<extra></extra>' },
    { x: [mac.y, mac.y], y: [mac.x_le, mac.x_le + mac.c], type: 'scatter', mode: 'lines', line: { color: SERIES()[1], width: 3 }, name: `OAV = ${fmt(mac.c)} m` },
  ], { xaxis: { title: 'y [m]' }, yaxis: { title: 'x [m] (akış ↓)', autorange: 'reversed', scaleanchor: 'x', scaleratio: 1 }, showlegend: true });
}

function drawSections(el, r) {
  plot(el, r.sections.map((s, i) => ({ x: s.x, y: s.z, type: 'scatter', mode: 'lines', name: `${s.label} (t/c ${(s.t_c * 100).toFixed(1)}%)`, line: { color: SERIES()[i], width: 2 } })),
    { xaxis: { title: 'x/c' }, yaxis: { title: 'z/c', scaleanchor: 'x', scaleratio: 1 }, showlegend: true });
}

export async function openLibrary(onLoad) {
  const list = await api.get('/api/designs');
  const body = modal(`<div class="page-head" style="margin-bottom:10px"><div><h1 style="font-size:18px">Tasarım kütüphanesi</h1><p>Kaydedilmiş tasarımlar <span class="mono small">data/designs/</span> klasöründe YAML olarak saklanır.</p></div><button class="btn" data-close-modal>Kapat</button></div>
    ${list.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Ad</th><th>Tip</th><th>Değiştirilme</th><th></th></tr></thead><tbody>
    ${list.map((d, i) => `<tr><td><b>${esc(d.name)}</b></td><td>${esc(state.meta.wing_types[d.type]?.label || d.type)}</td><td>${fmtDate(d.modified)}</td>
      <td class="num"><button class="btn sm primary" data-load="${i}">Yükle</button> <button class="btn sm ghost danger" data-del="${esc(d.name)}">${icon('trash')}</button></td></tr>`).join('')}
    </tbody></table></div>` : '<div class="empty">Henüz kayıtlı tasarım yok. Tasarım sayfasında “Kaydet” ile ekleyin.</div>'}`);
  body.onclick = async (e) => {
    const l = e.target.closest('[data-load]');
    if (l) {
      const d = list[+l.dataset.load].config;
      state.cfg = { ...state.cfg, ...clone(d) };
      delete state.cfg.optimization;
      saveState(); closeModal(); toast('Tasarım yüklendi', 'good'); onLoad?.();
    }
    const del = e.target.closest('[data-del]');
    if (del && confirm('Silinsin mi?')) { await api.del(`/api/designs/${encodeURIComponent(del.dataset.del)}`); openLibrary(onLoad); }
  };
}
