import { $, $$, api, state, saveState, toast, fmt, esc, icon, mountJob, stopMonitor, plot, SERIES, css, clone, empty } from '../core.js';
import { nacaParams } from './design.js';

const OBJ = [['max_LD', 'En yüksek L/D'], ['min_drag', 'En düşük sürükleme kuvveti [N]'], ['max_lift', 'En yüksek kaldırma kuvveti [N]'], ['min_CD', 'En düşük CD'], ['max_CL', 'En yüksek CL']];
const CONS = [['min_lift_N', 'Min. kaldırma', 'N', 300], ['max_drag_N', 'Maks. sürükleme', 'N', 50], ['min_CL', 'Min. CL', '', 0.3], ['min_LD', 'Min. L/D', '', 6],
  ['min_volume_m3', 'Min. iç hacim', 'm³', 0.015], ['min_area_m2', 'Min. alan', 'm²', 0.5], ['max_area_m2', 'Maks. alan', 'm²', 1.0], ['max_span_m', 'Maks. açıklık', 'm', 1.5], ['min_root_thickness', 'Min. kök t/c', '', 0.06]];

let ui = null;

function variableList() {
  const w = state.cfg.wing, meta = state.meta;
  const vars = [];
  for (const p of meta.wing_types[w.type].params) {
    const v = +(w[p.key] ?? p.default);
    const span = (p.max - p.min);
    let lo, hi;
    if (p.unit === '°') { lo = Math.max(p.min, v - 10); hi = Math.min(p.max, v + 10); }
    else if (p.max <= 1.0) { lo = Math.max(p.min, +(v - 0.15 * span).toFixed(3)); hi = Math.min(p.max, +(v + 0.15 * span).toFixed(3)); }
    else { lo = Math.max(p.min, +(v * 0.7).toFixed(3)); hi = Math.min(p.max, +(v * 1.3).toFixed(3)); }
    vars.push({ path: `wing.${p.key}`, label: p.label, unit: p.unit, value: v, lo, hi, on: ['le_sweep_deg', 'span', 'taper_ratio'].includes(p.key) });
  }
  for (const which of ['root', 'tip']) {
    const s = state.cfg.airfoil[which];
    if (s?.type === 'naca4') {
      const n = nacaParams(s);
      const nm = which === 'root' ? 'Kök' : 'Uç';
      vars.push({ path: `airfoil.${which}.thickness`, label: `${nm} profil kalınlığı t/c`, unit: '', value: n.t, lo: Math.max(0.03, +(n.t - 0.03).toFixed(3)), hi: +(n.t + 0.04).toFixed(3), on: which === 'root', naca: true });
      vars.push({ path: `airfoil.${which}.camber`, label: `${nm} profil kamberi`, unit: '', value: n.m, lo: 0, hi: 0.05, on: false, naca: true });
    }
  }
  return vars;
}

export async function render(main) {
  if (!ui || ui.type !== state.cfg.wing.type) {
    ui = { type: state.cfg.wing.type, vars: variableList(), objective: 'min_drag', mode: 'fixed_lift', lift: 300, alpha: 8, fidelity: 'vlm',
      method: 'differential_evolution', evals: 400, preset: 'kaba', cons: { min_volume_m3: { on: false, v: 0.015 } }, name: 'Optimizasyon' };
  }
  main.innerHTML = `
  <div class="page-head">
    <div><h1>Optimizasyon</h1><p>Planform ve profil parametrelerini otomatik olarak optimize edin. Hızlı çözücüyle binlerce tasarım dakikalar içinde taranır; en iyi tasarım tek tıkla 3B CFD ile doğrulanır.</p></div>
  </div>
  <div class="split-wide">
    <div class="grid">
      <div class="card"><h2>Tasarım değişkenleri <span class="sub">${esc(state.meta.wing_types[state.cfg.wing.type].label)} · başlangıç = mevcut tasarım</span></h2>
        <div class="tbl-wrap"><table class="tbl" id="vars"><thead><tr><th></th><th>Parametre</th><th class="num">Mevcut</th><th>Alt</th><th>Üst</th></tr></thead><tbody>
        ${ui.vars.map((v, i) => `<tr><td><input type="checkbox" data-i="${i}" data-f="on" ${v.on ? 'checked' : ''}></td><td>${esc(v.label)} <span class="muted small">${v.unit && v.unit !== '-' ? esc(v.unit) : ''}</span></td>
          <td class="num">${fmt(v.value)}</td><td><input type="number" step="any" data-i="${i}" data-f="lo" value="${v.lo}"></td><td><input type="number" step="any" data-i="${i}" data-f="hi" value="${v.hi}"></td></tr>`).join('')}
        </tbody></table></div>
        ${ui.vars.some((v) => v.naca) ? '' : '<div class="help small muted" style="margin-top:6px">Profil kalınlığı/kamberini optimize etmek için profili NACA 4 parametre moduna alın.</div>'}
      </div>
      <div class="card"><h2>Amaç</h2>
        <div class="field"><label>Amaç fonksiyonu</label><select id="obj">${OBJ.map(([k, l]) => `<option value="${k}" ${ui.objective === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
        <div class="field"><label>Değerlendirme koşulu</label><div class="seg" id="mode"><button data-m="fixed_lift" class="${ui.mode === 'fixed_lift' ? 'on' : ''}">Sabit kaldırma (seyir)</button><button data-m="fixed_alpha" class="${ui.mode === 'fixed_alpha' ? 'on' : ''}">Sabit hücum açısı</button></div></div>
        <div class="field ${ui.mode === 'fixed_lift' ? '' : 'hidden'}" id="f-lift"><label>Hedef kaldırma <span class="muted">N</span></label><input type="number" id="lift" value="${ui.lift}"><div class="help">Her tasarım bu kaldırmayı üreten açıda değerlendirilir (örn. uçak ağırlığı). Yalnızca hızlı çözücüde.</div></div>
        <div class="field ${ui.mode === 'fixed_alpha' ? '' : 'hidden'}" id="f-alpha"><label>Hücum açısı <span class="muted">°</span></label><input type="number" id="alpha" value="${ui.alpha}"></div>
      </div>
      <div class="card"><h2>Kısıtlar</h2>
        ${CONS.map(([k, l, u, d]) => { const c = ui.cons[k] || { on: false, v: d }; return `<div class="row" style="margin-bottom:6px"><label class="check" style="flex:1.4"><input type="checkbox" data-c="${k}" ${c.on ? 'checked' : ''}> ${l} ${u ? `<span class="muted small">${u}</span>` : ''}</label><input type="number" step="any" data-cv="${k}" value="${c.v}"></div>`; }).join('')}
      </div>
      <div class="card"><h2>Çözücü</h2>
        <div class="field"><label>Doğruluk seviyesi</label><div class="seg" id="fid"><button data-f="vlm" class="${ui.fidelity === 'vlm' ? 'on' : ''}">Hızlı çözücü</button><button data-f="openfoam" class="${ui.fidelity === 'openfoam' ? 'on' : ''}">3B CFD (yavaş)</button></div>
          <div class="help" id="fid-help"></div></div>
        <div class="row"><div class="field"><label>Yöntem</label><select id="method"><option value="differential_evolution">Differential evolution (global)</option><option value="nelder-mead">Nelder-Mead (yerel)</option><option value="powell">Powell (yerel)</option></select></div>
        <div class="field"><label>Maks. değerlendirme</label><input type="number" id="evals" min="5" max="20000" value="${ui.evals}"></div></div>
        <div class="field ${ui.fidelity === 'openfoam' ? '' : 'hidden'}" id="f-mesh"><label>CFD ağı</label><select id="preset">${Object.entries(state.meta.mesh_presets).map(([k, p]) => `<option value="${k}" ${ui.preset === k ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></div>
        <div class="field"><label>Çalışma adı</label><input type="text" id="name" value="${esc(ui.name)}"></div>
        <button class="btn primary" id="start" style="width:100%">${icon('play')} Optimizasyonu başlat</button>
      </div>
    </div>
    <div class="grid">
      <div id="job"></div>
      <div class="card"><h2>Yakınsama</h2><div class="chart tall" id="conv">${empty('Optimizasyon başlayınca her değerlendirme ve en iyi değer burada görünür.', 'optimize')}</div></div>
      <div id="best"></div>
    </div>
  </div>`;

  $('#method').value = ui.method;
  const fidHelp = () => { $('#fid-help').textContent = ui.fidelity === 'vlm' ? 'VLM + Polhamus + viskoz direnç: değerlendirme başına ~0.3 s.' : 'Her değerlendirme tam bir OpenFOAM çözümüdür (dakikalar). 2-3 değişken ve Nelder-Mead ile 20-40 değerlendirme önerilir.'; };
  fidHelp();
  $('#vars').oninput = (e) => { const i = e.target.dataset.i; if (i === undefined) return; const f = e.target.dataset.f; ui.vars[i][f] = f === 'on' ? e.target.checked : +e.target.value; };
  $('#vars').onchange = $('#vars').oninput;
  $('#obj').onchange = (e) => { ui.objective = e.target.value; };
  $('#mode').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; ui.mode = b.dataset.m; $$('#mode button').forEach((x) => x.classList.toggle('on', x === b)); $('#f-lift').classList.toggle('hidden', ui.mode !== 'fixed_lift'); $('#f-alpha').classList.toggle('hidden', ui.mode !== 'fixed_alpha'); };
  $('#fid').onclick = (e) => {
    const b = e.target.closest('button'); if (!b) return; ui.fidelity = b.dataset.f; $$('#fid button').forEach((x) => x.classList.toggle('on', x === b)); $('#f-mesh').classList.toggle('hidden', ui.fidelity !== 'openfoam'); fidHelp();
    if (ui.fidelity === 'openfoam') { ui.method = 'nelder-mead'; $('#method').value = 'nelder-mead'; ui.evals = Math.min(ui.evals, 30); $('#evals').value = ui.evals; if (ui.mode === 'fixed_lift') { $('#mode [data-m="fixed_alpha"]').click(); } }
  };
  $('#lift').oninput = (e) => { ui.lift = +e.target.value; };
  $('#alpha').oninput = (e) => { ui.alpha = +e.target.value; };
  $('#method').onchange = (e) => { ui.method = e.target.value; };
  $('#evals').oninput = (e) => { ui.evals = +e.target.value; };
  $('#preset').onchange = (e) => { ui.preset = e.target.value; };
  $('#name').oninput = (e) => { ui.name = e.target.value; };
  main.querySelectorAll('[data-c]').forEach((c) => { c.onchange = () => { const k = c.dataset.c; ui.cons[k] = { ...(ui.cons[k] || {}), on: c.checked, v: +$(`[data-cv="${k}"]`).value }; }; });
  main.querySelectorAll('[data-cv]').forEach((c) => { c.oninput = () => { const k = c.dataset.cv; ui.cons[k] = { ...(ui.cons[k] || { on: false }), v: +c.value }; }; });

  $('#start').onclick = async () => {
    const variables = {};
    ui.vars.filter((v) => v.on).forEach((v) => { variables[v.path] = [Math.min(v.lo, v.hi), Math.max(v.lo, v.hi)]; });
    if (!Object.keys(variables).length) { toast('En az bir değişken seçin', 'bad'); return; }
    const constraints = {};
    for (const [k, c] of Object.entries(ui.cons)) if (c.on) constraints[k] = c.v;
    // NACA profilleri sürekli parametre moduna çevir (optimizasyon yolları için)
    const cfg = clone(state.cfg);
    for (const w of ['root', 'tip']) if (cfg.airfoil[w]?.type === 'naca4') { const n = nacaParams(cfg.airfoil[w]); cfg.airfoil[w] = { type: 'naca4', camber: n.m, camber_pos: n.p, thickness: n.t }; }
    const optimization = { fidelity: ui.fidelity, method: ui.method, mode: ui.fidelity === 'vlm' ? ui.mode : 'fixed_alpha', lift_target_N: ui.lift, alpha_deg: ui.alpha,
      objective: ui.objective, max_evals: ui.evals, constraints, variables, seed: 1 };
    try {
      const r = await api.post('/api/optimize/start', { config: cfg, optimization, name: ui.name, mesh: ui.fidelity === 'openfoam' ? { preset: ui.preset } : null });
      state.activeJobs.opt = r.job_id; saveState();
      watch(r.job_id);
    } catch (e) { toast(e.message, 'bad'); }
  };
  if (state.activeJobs.opt) watch(state.activeJobs.opt);
}

function watch(id) {
  mountJob($('#job'), id, {
    onUpdate: (j) => {
      const h = j.data?.history; if (!h?.length) return;
      const ok = h.filter((r) => r.ok);
      let best = Infinity; const bx = [], by = [];
      for (const r of ok) { if (r.f < best) best = r.f; bx.push(r.eval); by.push(best); }
      const fs = ok.map((r) => r.f).sort((a, b) => a - b);
      const hi = fs[Math.floor(fs.length * 0.9)] ?? fs[fs.length - 1];
      plot($('#conv'), [
        { x: ok.map((r) => r.eval), y: ok.map((r) => r.f), type: 'scattergl', mode: 'markers', name: 'değerlendirme', marker: { size: 5, color: css('--border-strong') } },
        { x: bx, y: by, type: 'scatter', mode: 'lines', name: 'en iyi', line: { color: SERIES()[0], width: 2.5, shape: 'hv' } },
      ], { xaxis: { title: 'değerlendirme #' }, yaxis: { title: 'amaç (+ceza)', range: [fs[0] - 0.05 * Math.abs(hi - fs[0]), hi + 0.05 * Math.abs(hi - fs[0])] }, showlegend: true });
      const b = j.data.best;
      if (b?.CL !== undefined) {
        $('#best').innerHTML = `<div class="stats">${[['En iyi L/D', fmt(b.L_over_D, 3)], ['CL', fmt(b.CL)], ['CD', fmt(b.CD)], ['Kaldırma', fmt(b.lift_N) + ' N'], ['Sürükleme', fmt(b.drag_N) + ' N'], ['α', fmt(b.alpha_deg) + '°']]
          .map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('')}</div>`;
      }
    },
    onDone: (j) => { if (j.status === 'done') showBest(j.result); },
    onLost: () => { delete state.activeJobs.opt; saveState(); },
  });
}

function showBest(res) {
  const r = res.result;
  const rows = Object.entries(res.variables).map(([k, v]) => {
    const vv = ui?.vars.find((x) => x.path === k);
    return `<tr><td>${esc(vv?.label || k)}</td><td class="num">${fmt(vv?.value)}</td><td class="num"><b>${fmt(v)}</b></td></tr>`;
  }).join('');
  $('#best').innerHTML = `<div class="card"><h2>En iyi tasarım <span class="sub">${res.n_evals} değerlendirme · ${fmt(res.elapsed_s, 3)} s</span></h2>
    <div class="stats" style="margin-bottom:12px">${[['L/D', fmt(r.L_over_D, 3)], ['CL', fmt(r.CL)], ['CD', fmt(r.CD)], ['Kaldırma', fmt(r.lift_N) + ' N'], ['Sürükleme', fmt(r.drag_N) + ' N'], ['α', fmt(r.alpha_deg) + '°']]
      .map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('')}</div>
    ${r.violation > 1e-9 ? '<div class="alert warn">⚠︎ Kısıtlar tam sağlanamadı; sınırları gevşetin veya değerlendirme sayısını artırın.</div>' : ''}
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Değişken</th><th class="num">Başlangıç</th><th class="num">Optimum</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="actions" style="margin-top:12px"><button class="btn primary" id="apply">${icon('check')} Tasarıma uygula</button>
      <button class="btn" id="verify">${icon('cfd')} Uygula ve CFD ile doğrula</button>
      <a class="btn" href="/api/studies/${res.study_id}/report" target="_blank">${icon('report')} Rapor</a></div></div>`;
  const apply = () => {
    const b = res.best_config;
    state.cfg.wing = b.wing; state.cfg.airfoil = b.airfoil; saveState(); ui = null;
    toast('En iyi tasarım uygulandı', 'good');
  };
  $('#apply').onclick = () => { apply(); location.hash = '#/tasarim'; };
  $('#verify').onclick = () => { apply(); state.cfdSource = 'wing'; saveState(); location.hash = '#/cfd'; toast(`CFD sayfasında α = ${fmt(r.alpha_deg)}° girip başlatın`); };
}

export function leave() { stopMonitor($('#job')); }
