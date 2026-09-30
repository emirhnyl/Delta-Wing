import { $, api, state, toast, fmt, esc, icon, plot, SERIES, css, polarCharts, resultsTable, downloadCSV, empty, line } from '../core.js';

export async function render(main) {
  const w = state.cfg.wing;
  const tl = state.meta.wing_types[w.type]?.label || w.type;
  main.innerHTML = `
  <div class="page-head">
    <div><h1>Hızlı Analiz</h1><p>Vortex Lattice + Polhamus girdap kaldırması + viskoz sürtünme ile saniyeler içinde polar. Tasarım uzayını taramak ve eğilimleri görmek içindir; kesin değerler için 3B CFD kullanın.</p></div>
    <div class="actions"><span class="badge info">${esc(tl)}</span><a class="btn" href="#/tasarim">${icon('design')} Tasarımı düzenle</a></div>
  </div>
  <div class="card" style="margin-bottom:16px">
    <div class="row" style="align-items:flex-end;flex-wrap:wrap">
      <div class="field" style="margin:0"><label>α başlangıç [°]</label><input type="number" id="a0" value="-2" step="1"></div>
      <div class="field" style="margin:0"><label>α bitiş [°]</label><input type="number" id="a1" value="20" step="1"></div>
      <div class="field" style="margin:0"><label>Adım [°]</label><input type="number" id="da" value="2" step="0.5" min="0.25"></div>
      <div class="field" style="margin:0"><label>Yük dağılımı için α [°]</label><input type="number" id="sa" value="8" step="1"></div>
      <div class="field" style="margin:0"><label>Çalışma adı</label><input type="text" id="name" value="Hızlı analiz – ${esc(tl)}"></div>
      <div class="actions" style="flex:0 0 auto">
        <button class="btn primary" id="run">${icon('play')} Analiz et</button>
        <button class="btn" id="save" disabled>${icon('save')} Çalışma olarak kaydet</button>
        <button class="btn" id="csv" disabled>${icon('download')} CSV</button>
      </div>
    </div>
  </div>
  <div id="out">${empty('Parametreleri seçip <b>Analiz et</b>e basın.')}</div>`;

  const alphas = () => {
    const a0 = +$('#a0').value, a1 = +$('#a1').value, da = Math.max(0.25, +$('#da').value);
    const out = []; for (let a = a0; a <= a1 + 1e-9; a += da) out.push(+a.toFixed(4));
    return out.slice(0, 200);
  };
  let last = null;
  const run = async (save = false) => {
    $('#run').disabled = true;
    try {
      const r = await api.post('/api/quick', { config: state.cfg, alphas: alphas(), span_alpha: +$('#sa').value, save, name: $('#name').value });
      last = r; state.lastQuick = r;
      show(r);
      $('#save').disabled = false; $('#csv').disabled = false;
      if (save) toast('Çalışma kaydedildi (Sonuçlar sayfası)', 'good');
    } catch (e) { toast(e.message, 'bad'); } finally { $('#run').disabled = false; }
  };
  $('#run').onclick = () => run(false);
  $('#save').onclick = () => run(true);
  $('#csv').onclick = () => downloadCSV(last?.results, 'hizli_polar.csv');
  if (state.lastQuick) { last = state.lastQuick; show(last); $('#save').disabled = false; $('#csv').disabled = false; }
  else run(false);
}

function show(r) {
  const res = r.results, m = r.model;
  const best = res.reduce((b, x) => (x.L_over_D > (b?.L_over_D ?? -1e9) ? x : b), null);
  const st = (k, v, u = '', hero = '') => `<div class="stat ${hero}"><div class="k">${k}</div><div class="v">${v}<span class="u">${u}</span></div></div>`;
  $('#out').innerHTML = `
    <div class="stats" style="margin-bottom:16px">
      ${st('En yüksek L/D', fmt(best?.L_over_D, 3), ` @ α=${fmt(best?.alpha_deg)}°`, 'hero')}
      ${st('CLα (potansiyel)', fmt(m.Kp, 3), ' /rad')}
      ${st('Kv (girdap)', m.vortex_lift ? fmt(m.Kv, 3) : 'kapalı')}
      ${st('α₀ (sıfır kaldırma)', fmt(m.alpha0_deg, 3), '°')}
      ${st('CD₀ (sürtünme+form)', fmt(m.CD0, 3))}
      ${st('Açıklık verimi e', fmt(r.span.e_span_efficiency, 3))}
      ${st('Re (OAV)', fmt(m.Re_mac / 1e6, 3), ' ×10⁶')}
    </div>
    <div id="pc"></div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h2>Açıklık boyunca yük dağılımı <span class="sub">α = ${fmt(r.span.alpha_deg)}° · potansiyel çözüm</span></h2><div class="chart" id="span"></div></div>
      <div class="card"><h2>Yerel kesit kaldırma katsayısı <span class="sub">uç stall eğilimi</span></h2><div class="chart" id="cll"></div></div>
    </div>
    <div class="card" style="margin-top:16px"><h2>Sonuç tablosu <span class="sub">${esc(res[0]?.method || '')} · q = ${fmt(res[0]?.q_Pa)} Pa · S = ${fmt(res[0]?.area_m2)} m²</span></h2>
      ${resultsTable(res, [['CD0', 'CD₀'], ['CDi', 'CDi'], ['CL_potential', 'CL pot.'], ['CL_vortex', 'CL girdap']])}</div>
    ${m.vortex_lift ? '<div class="alert info" style="margin-top:12px">Polhamus modeli hücum kenarı emmesinin tamamen kaybolduğunu varsayar (keskin kenar). Yuvarlak burunlu profillerde sürüklemeyi yüksek tahmin edebilir; girdap patlaması ve stall (~20° üzeri) modellenmez.</div>' : ''}`;
  polarCharts($('#pc'), [{ name: 'VLM + Polhamus', rows: res }]);
  const s = r.span;
  plot('#span', [
    line(s.y, s.cl_c_over_cref, 'c·cl / c_ref', 0, { mode: 'lines' }),
    line(s.y, s.elliptic, 'eliptik (aynı CL)', 2, { mode: 'lines', line: { color: SERIES()[2], width: 2, dash: 'dash' } }),
  ], { xaxis: { title: 'y [m]' }, yaxis: { title: 'c·cl / c_ref' }, showlegend: true });
  plot('#cll', [line(s.y, s.cl_local, 'cl yerel', 1, { mode: 'lines' })], { xaxis: { title: 'y [m]' }, yaxis: { title: 'cl' }, showlegend: false });
}
