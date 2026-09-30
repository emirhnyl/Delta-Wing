// CFD akış görselleştirme bileşeni: yüzey Cp + 3B akış çizgileri, kesit haritaları, görüntü galerisi
import { $$, api, toast, fmt, esc, icon, css, baseLayout, plotConfig, mountJob, empty } from './core.js';

// matplotlib ile aynı (PNG'lerle tutarlı): Cp negatif (emme) = kırmızı, pozitif = mavi
const RDBU = [[0, '#67001f'], [0.1, '#b2182b'], [0.2, '#d6604d'], [0.3, '#f4a582'], [0.4, '#fddbc7'], [0.5, '#f7f7f7'],
  [0.6, '#d1e5f0'], [0.7, '#92c5de'], [0.8, '#4393c3'], [0.9, '#2166ac'], [1, '#053061']];
const BLUES_R = [[0, '#08306b'], [0.25, '#2171b5'], [0.5, '#6baed6'], [0.75, '#c6dbef'], [1, '#f7fbff']];
const FIELDS = {
  cp: { label: 'Basınç katsayısı Cp', short: 'Cp', scale: RDBU, sym: true },
  umag: { label: 'Hız |U| / U∞', short: '|U|/U∞', scale: 'Viridis' },
  cp0: { label: 'Toplam basınç Cp0 (kayıp / girdap)', short: 'Cp0', scale: BLUES_R },
};
const cache = new Map();
async function getJSON(url) {
  if (!cache.has(url)) cache.set(url, fetch(url).then((r) => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); }));
  return cache.get(url);
}

export async function mountFlowViz(el, sid, opts = {}) {
  el.innerHTML = `<div class="card"><h2>Akış görselleştirmesi <span class="sub">yüzey basıncı, akış çizgileri, kesitler</span></h2><div data-v="body"><div class="empty"><span class="spinner"></span>Yükleniyor…</div></div></div>`;
  const body = el.querySelector('[data-v="body"]');
  let info;
  try { info = await api.get(`/api/studies/${sid}/viz`); } catch (e) { body.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; return; }
  const cases = info.cases;
  if (!cases.length) { body.innerHTML = empty('Bu çalışmada CFD vakası bulunamadı.', 'cfd'); return; }
  const st = { ci: Math.max(0, cases.findIndex((c) => c.ready)), view: opts.view || '3d', slice: null, field: 'cp0', streams: true, surface: true, lines3d: true };
  if (st.ci < 0) st.ci = 0;

  const render = () => {
    const c = cases[st.ci];
    body.innerHTML = `
      <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:12px">
        <div class="seg" data-v="alpha">${cases.map((k, i) => `<button data-i="${i}" class="${i === st.ci ? 'on' : ''}">α = ${fmt(k.alpha_deg)}°${k.ready ? '' : ' ·'}</button>`).join('')}</div>
        <div class="seg" data-v="view">${[['3d', '3B: yüzey + akış çizgileri'], ['slice', 'Kesitler'], ['img', 'Görüntüler']].map(([k, l]) => `<button data-k="${k}" class="${st.view === k ? 'on' : ''}">${l}</button>`).join('')}</div>
      </div>
      <div data-v="content"></div>`;
    body.querySelector('[data-v="alpha"]').onclick = (e) => { const b = e.target.closest('button'); if (b) { st.ci = +b.dataset.i; render(); } };
    body.querySelector('[data-v="view"]').onclick = (e) => { const b = e.target.closest('button'); if (b) { st.view = b.dataset.k; render(); } };
    const content = body.querySelector('[data-v="content"]');
    if (!c.ready) return renderBuild(content, c);
    if (st.view === '3d') render3d(content, c);
    else if (st.view === 'slice') renderSlice(content, c);
    else renderImages(content, c);
  };

  function renderBuild(content, c) {
    content.innerHTML = `<div class="empty">${icon('cfd')}<div>α = ${fmt(c.alpha_deg)}° için akış görselleştirmesi henüz oluşturulmamış.</div>
      <button class="btn primary" data-v="build">${icon('play')} Görselleştirmeyi oluştur</button><div class="small muted">Ağ boyutuna göre 10 sn – birkaç dk sürer.</div></div><div data-v="job"></div>`;
    content.querySelector('[data-v="build"]').onclick = async () => {
      const r = await api.post(`/api/studies/${sid}/viz/${c.case}/build`);
      mountJob(content.querySelector('[data-v="job"]'), r.job_id, { openLog: true, onDone: async (j) => {
        if (j.status === 'done') { const ni = await api.get(`/api/studies/${sid}/viz`); cases.splice(0, cases.length, ...ni.cases); render(); }
      } });
    };
  }

  const base = (c) => `/api/studies/${sid}/viz/${c.case}`;

  async function render3d(content, c) {
    content.innerHTML = `<div class="row" style="gap:16px;flex-wrap:wrap;margin-bottom:8px">
        <label class="check" style="flex:0 0 auto"><input type="checkbox" data-t="surface" ${st.surface ? 'checked' : ''}> Yüzey Cp</label>
        <label class="check" style="flex:0 0 auto"><input type="checkbox" data-t="lines3d" ${st.lines3d ? 'checked' : ''}> Akış çizgileri</label>
        <span class="small muted" style="flex:1 1 auto;text-align:right">Döndür: sürükle · Yakınlaştır: tekerlek · PNG: sağ üstteki kamera simgesi</span></div>
      <div class="chart" style="height:min(72vh,680px)" data-v="plot"><div class="empty"><span class="spinner"></span>Veri yükleniyor…</div></div>
      <p class="small muted" style="margin:8px 0 0">Yüzey rengi Cp (kırmızı = emme, mavi = basınç), çizgi rengi yerel hız |U|/U∞. Delta kanatlarda hücum kenarından ayrılan akış üst yüzeyde girdaba sarılır.</p>`;
    $$('[data-t]', content).forEach((cb) => { cb.onchange = () => { st[cb.dataset.t] = cb.checked; draw(); }; });
    let surf, sl;
    try { [surf, sl] = await Promise.all([getJSON(`${base(c)}/surface.json`), getJSON(`${base(c)}/streamlines.json`)]); } catch (e) { content.querySelector('[data-v="plot"]').innerHTML = `<div class="alert bad">Veri okunamadı: ${esc(e.message)}</div>`; return; }
    const plotEl = content.querySelector('[data-v="plot"]');
    function draw() {
      const traces = [];
      const m = Math.max(Math.abs(surf.cp_range[0]), Math.abs(surf.cp_range[1]));
      if (st.surface) {
        traces.push({ type: 'mesh3d', x: surf.x, y: surf.y, z: surf.z, i: surf.i, j: surf.j, k: surf.k, intensity: surf.cp, intensitymode: 'cell',
          colorscale: RDBU, cmin: -m, cmax: m, flatshading: false, lighting: { ambient: 0.65, diffuse: 0.6, specular: 0.15 },
          colorbar: { title: { text: 'Cp', side: 'right' }, x: 1.0, len: 0.45, y: 0.75, thickness: 12, tickfont: { size: 10 } }, name: 'Cp',
          hovertemplate: 'Cp = %{intensity:.3f}<extra></extra>' });
      } else {
        traces.push({ type: 'mesh3d', x: surf.x, y: surf.y, z: surf.z, i: surf.i, j: surf.j, k: surf.k, color: '#bdbcb6', hoverinfo: 'skip', lighting: { ambient: 0.6 } });
      }
      if (st.lines3d) {
        const smax = Math.max(1.2, ...sl.lines.map((l) => Math.max(...l.s.filter((v) => v !== null))));
        sl.lines.forEach((l, idx) => traces.push({ type: 'scatter3d', mode: 'lines', x: l.x, y: l.y, z: l.z, hoverinfo: 'skip', showlegend: false,
          line: { width: 2.5, color: l.s, colorscale: 'Viridis', cmin: 0.4, cmax: smax, showscale: idx === 0,
            colorbar: idx === 0 ? { title: { text: '|U|/U∞', side: 'right' }, x: 1.0, len: 0.45, y: 0.25, thickness: 12, tickfont: { size: 10 } } : undefined } }));
      }
      const grid = css('--grid');
      const ax = (t) => ({ title: { text: t, font: { size: 11, color: css('--text-2') } }, gridcolor: grid, zerolinecolor: grid, showbackground: false, tickfont: { size: 10, color: css('--text-3') } });
      const xs = surf.x, ys = surf.y;
      let x0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (let q = 0; q < xs.length; q++) { if (xs[q] < x0) x0 = xs[q]; if (xs[q] > x1) x1 = xs[q]; if (ys[q] > y1) y1 = ys[q]; }
      const L = x1 - x0;
      Plotly.react(plotEl, traces, {
        paper_bgcolor: 'rgba(0,0,0,0)', margin: { l: 0, r: 0, t: 0, b: 0 }, showlegend: false, font: { color: css('--text') },
        scene: { aspectmode: 'data', xaxis: { ...ax('x [m] (akış →)'), range: [x0 - 0.45 * L, x1 + 1.05 * L] }, yaxis: ax('y [m]'), zaxis: ax('z [m]'),
                 camera: { eye: { x: -0.9, y: -1.25, z: 0.75 }, center: { x: 0, y: 0, z: -0.1 } } },
      }, { ...plotConfig, toImageButtonOptions: { format: 'png', filename: `akis_${c.case}`, scale: 2 } });
    }
    plotEl.innerHTML = '';
    draw();
  }

  async function renderSlice(content, c) {
    const meta = c.meta;
    st.slice = st.slice && meta.slices.some((s) => s.name === st.slice) ? st.slice : meta.slices[meta.slices.length > 4 ? 4 : 0].name;
    content.innerHTML = `<div class="row" style="gap:10px;flex-wrap:wrap;margin-bottom:8px;align-items:flex-end">
        <div class="field" style="margin:0;flex:2 1 280px"><label>Kesit düzlemi</label><select data-v="slice">${meta.slices.map((s) => `<option value="${s.name}" ${s.name === st.slice ? 'selected' : ''}>${esc(s.label)}</option>`).join('')}</select></div>
        <div class="field" style="margin:0;flex:1 1 200px"><label>Alan</label><select data-v="field">${Object.entries(FIELDS).map(([k, f]) => `<option value="${k}" ${k === st.field ? 'selected' : ''}>${f.label}</option>`).join('')}</select></div>
        <label class="check" style="flex:0 0 auto"><input type="checkbox" data-v="streams" ${st.streams ? 'checked' : ''}> Kesit içi akış çizgileri</label></div>
      <div class="chart" style="height:min(62vh,580px)" data-v="plot"><div class="empty"><span class="spinner"></span>Yükleniyor…</div></div>
      <p class="small muted" style="margin:8px 0 0">Çapraz kesitlerde (y-z) toplam basınç Cp0 alanı girdap çekirdeklerini ve izi gösterir; kesit içi çizgiler dönen akışı (girdap) ortaya çıkarır. Veter kesitlerinde (x-z) hız ve basınç dağılımı görülür.</p>`;
    const slices = await getJSON(`${base(c)}/slices.json`);
    const plotEl = content.querySelector('[data-v="plot"]');
    const draw = () => {
      const s = slices.find((x) => x.name === st.slice);
      const f = FIELDS[st.field];
      let zmin, zmax;
      const vals = s[st.field].flat().filter((v) => v !== null).sort((a, b) => a - b);
      const pct = (p) => vals[Math.min(vals.length - 1, Math.floor(p * vals.length))];
      if (f.sym) { const mm = Math.max(Math.abs(pct(0.01)), Math.abs(pct(0.99))); zmin = -mm; zmax = mm; }
      else if (st.field === 'cp0') { zmin = pct(0.01); zmax = 1.02; }
      else { zmin = pct(0.005); zmax = pct(0.995); }
      const traces = [{ type: 'heatmap', x: s.a, y: s.b, z: s[st.field], colorscale: f.scale, zmin, zmax, zsmooth: 'best', hoverongaps: false,
        colorbar: { title: { text: f.short, side: 'right' }, thickness: 12 },
        hovertemplate: `${s.a_label[0]} = %{x:.3f} m<br>${s.b_label[0]} = %{y:.3f} m<br>${f.label} = %{z:.3f}<extra></extra>` }];
      if (st.streams && s.streams.length) {
        const X = [], Y = [];
        s.streams.forEach((ln) => { X.push(...ln[0], null); Y.push(...ln[1], null); });
        traces.push({ type: 'scattergl', mode: 'lines', x: X, y: Y, line: { color: st.field === 'cp' ? 'rgba(20,20,20,0.55)' : 'rgba(255,255,255,0.7)', width: 1 }, hoverinfo: 'skip', showlegend: false });
      }
      if (s.outline.length) {
        const X = [], Y = [];
        s.outline.forEach((q) => { X.push(q[0], q[2], null); Y.push(q[1], q[3], null); });
        traces.push({ type: 'scattergl', mode: 'lines', x: X, y: Y, line: { color: css('--text'), width: 2 }, hoverinfo: 'skip', showlegend: false });
      }
      Plotly.react(plotEl, traces, baseLayout({ xaxis: { title: s.a_label, range: [s.a[0], s.a[s.a.length - 1]] }, yaxis: { title: s.b_label, scaleanchor: 'x', scaleratio: 1, range: [s.b[0], s.b[s.b.length - 1]] },
        margin: { l: 56, r: 20, t: 16, b: 44 }, showlegend: false }), { ...plotConfig, toImageButtonOptions: { format: 'png', filename: `${c.case}_${s.name}_${st.field}`, scale: 2 } });
    };
    plotEl.innerHTML = '';
    content.querySelector('[data-v="slice"]').onchange = (e) => { st.slice = e.target.value; if (st.slice.startsWith('cross') && st.field === 'umag') st.field = 'cp0'; draw(); };
    content.querySelector('[data-v="field"]').onchange = (e) => { st.field = e.target.value; draw(); };
    content.querySelector('[data-v="streams"]').onchange = (e) => { st.streams = e.target.checked; draw(); };
    draw();
  }

  function renderImages(content, c) {
    const imgs = c.meta.images;
    const cap = (f) => f.replace('.png', '').replace('slice_', '').replace('surface_cp', 'Yüzey Cp').replace('streamlines_3d', '3B akış çizgileri')
      .replace('span', '%').replace('cross_mid', 'çapraz kesit – orta').replace('cross_te', 'çapraz kesit – firar kenarı').replace('cross_wake', 'çapraz kesit – iz')
      .replace('_cp0', ' · Cp0').replace('_cp', ' · Cp').replace('_umag', ' · |U|');
    content.innerHTML = `<div class="grid g3">${imgs.map((f) => `<a href="${base(c)}/${f}" target="_blank" class="card" style="padding:8px;text-decoration:none;color:inherit">
        <img src="${base(c)}/${f}" loading="lazy" style="width:100%;border-radius:6px;display:block"><div class="small muted" style="margin-top:6px">${esc(cap(f))}</div></a>`).join('')}</div>
      <p class="small muted" style="margin:10px 0 0">Tüm görüntüler çalışmanın ZIP dosyasında ve raporda da bulunur. Tam boyut için görüntüye tıklayın.</p>`;
  }

  render();
}
