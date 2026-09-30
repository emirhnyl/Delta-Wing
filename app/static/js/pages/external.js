import { $, $$, api, state, saveState, toast, fmt, esc, icon, empty, debounce } from '../core.js';
import { draw3D } from './design.js';

const AX_LABEL = { '+x': '+X', '-x': '−X', '+y': '+Y', '-y': '−Y', '+z': '+Z', '-z': '−Z' };

export async function render(main) {
  main.innerHTML = `
  <div class="page-head">
    <div><h1>Harici Geometri</h1><p>CAD'den dışa aktarılmış herhangi bir gövdeyi (kanat, İHA, uçak, füze …) STL veya OBJ olarak yükleyin, yönünü ve birimini ayarlayın, ardından 3B CFD ile kaldırma ve sürükleme katsayılarını hesaplayın.</p></div>
    <div class="actions"><button class="btn primary" id="to-cfd" disabled>${icon('cfd')} CFD analizine gönder</button></div>
  </div>
  <div class="split">
    <div class="grid">
      <div class="card"><h2>Model yükle</h2>
        <label class="dropzone" id="drop">${icon('upload')}<div><b>STL / OBJ dosyasını sürükleyin</b> veya tıklayın</div><div class="small">ASCII veya binary STL · kapalı (su geçirmez) yüzey önerilir</div><input type="file" id="file" accept=".stl,.obj" hidden></label>
        <div class="field" style="margin-top:12px"><label>Daha önce yüklenenler</label><select id="prev"><option value="">—</option></select></div>
      </div>
      <div class="card" id="tf-card"><h2>Yönlendirme ve birim</h2>
        <div class="field"><label>Model birimi</label><select id="unit">${state.meta.units.map((u) => `<option>${u}</option>`).join('')}</select></div>
        <div class="row"><div class="field"><label>Akış yönündeki model ekseni</label><select id="fwd">${state.meta.axes.map((a) => `<option value="${a}">${AX_LABEL[a]}</option>`).join('')}</select><div class="help">Burundan kuyruğa bakan eksen</div></div>
        <div class="field"><label>Yukarı ekseni</label><select id="up">${state.meta.axes.map((a) => `<option value="${a}">${AX_LABEL[a]}</option>`).join('')}</select></div></div>
        <div class="lbl" style="margin-bottom:4px">Ek dönüş [°] (x, y, z)</div>
        <div class="row" style="margin-bottom:10px"><input type="number" id="rx" step="1" value="0"><input type="number" id="ry" step="1" value="0"><input type="number" id="rz" step="1" value="0"></div>
        <label class="check"><input type="checkbox" id="center" checked> Burnu x = 0'a taşı, y/z'de ortala</label>
        <label class="check"><input type="checkbox" id="sym"> Yarım model (gövde y = 0'a göre simetrik) · ~2× hızlı</label>
      </div>
      <div class="card"><h2>Referans büyüklükler</h2>
        <div class="field"><label>Referans alan S <span class="muted">m²</span></label><input type="number" id="aref" step="any" min="0">
          <div class="actions" style="margin-top:6px"><button class="btn sm" data-ref="planform_area_m2">Planform</button><button class="btn sm" data-ref="frontal_area_m2">Ön alan</button><button class="btn sm" data-ref="wetted_area_m2">Islak alan</button></div>
          <div class="help">Kanatlarda planform alanı, gövdelerde genelde ön alan kullanılır. Katsayılar bu alana göre normalize edilir.</div></div>
        <div class="field"><label>Referans uzunluk (moment) <span class="muted">m</span></label><input type="number" id="lref" step="any" min="0"></div>
        <div class="field"><label>Moment referans noktası (x, y, z) <span class="muted">m</span></label><div class="row"><input type="number" id="cx" step="any"><input type="number" id="cy" step="any"><input type="number" id="cz" step="any"></div></div>
      </div>
    </div>
    <div class="grid">
      <div id="warn"></div>
      <div class="stats" id="stats"></div>
      <div class="card"><h2>Önizleme <span class="sub">CFD koordinatları: x akış yönü (turuncu ok), z yukarı</span></h2><div id="view" class="chart xl">${empty('Bir model yükleyin.', 'external')}</div></div>
    </div>
  </div>`;

  const ext = () => state.external;
  const list = await api.get('/api/external/list');
  $('#prev').innerHTML = '<option value="">—</option>' + list.map((g) => `<option value="${g.id}" ${ext()?.id === g.id ? 'selected' : ''}>${esc(g.name)}</option>`).join('');

  const drop = $('#drop');
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
  $('#file').onchange = (e) => { const f = e.target.files[0]; if (f) upload(f); };
  $('#prev').onchange = (e) => {
    const g = list.find((x) => x.id === e.target.value); if (!g) return;
    state.external = { id: g.id, name: g.name, transform: { unit: 'm', forward: '+x', up: '+z', center: true, symmetric: false, rotate_deg: [0, 0, 0] } };
    fillForm(); prepare(true);
  };

  async function upload(f) {
    drop.classList.add('over');
    try {
      const r = await api.upload('/api/external/upload', f);
      state.external = { id: r.id, name: r.name, transform: { unit: r.unit_guess, forward: '+x', up: '+z', center: true, symmetric: false, rotate_deg: [0, 0, 0] } };
      toast(`${r.name} yüklendi · ${r.n_triangles.toLocaleString('tr-TR')} üçgen · birim tahmini: ${r.unit_guess}`, 'good');
      fillForm(); prepare(true);
    } catch (e) { toast(e.message, 'bad'); } finally { drop.classList.remove('over'); }
  }

  function fillForm() {
    const e = ext(); if (!e) return;
    const t = e.transform;
    $('#unit').value = t.unit; $('#fwd').value = t.forward; $('#up').value = t.up;
    [$('#rx').value, $('#ry').value, $('#rz').value] = t.rotate_deg || [0, 0, 0];
    $('#center').checked = t.center; $('#sym').checked = t.symmetric;
    if (e.aref) $('#aref').value = +e.aref.toPrecision(5);
    if (e.lref) $('#lref').value = +e.lref.toPrecision(5);
    if (e.cofr) [$('#cx').value, $('#cy').value, $('#cz').value] = e.cofr.map((v) => +(+v).toPrecision(5));
  }
  const readForm = () => {
    const e = ext(); if (!e) return;
    e.transform = { unit: $('#unit').value, forward: $('#fwd').value, up: $('#up').value, center: $('#center').checked,
      symmetric: $('#sym').checked, rotate_deg: [+$('#rx').value || 0, +$('#ry').value || 0, +$('#rz').value || 0] };
  };

  let lastMetrics = null;
  async function prepare(resetRefs = false) {
    const e = ext(); if (!e) return;
    try {
      const r = await api.post('/api/external/prepare', { id: e.id, transform: e.transform });
      lastMetrics = r.metrics;
      e.metrics = r.metrics;
      if (resetRefs || !e.aref) { e.aref = r.suggest.aref; e.lref = r.suggest.lref; e.cofr = r.suggest.cofr; }
      saveState(); fillForm();
      const m = r.metrics;
      const st = (k, v, u = '') => `<div class="stat"><div class="k">${k}</div><div class="v">${v}<span class="u">${u}</span></div></div>`;
      $('#stats').innerHTML = st('Boy (x)', fmt(m.length_x_m), ' m') + st('Genişlik (y)', fmt(m.span_y_m), ' m') + st('Yükseklik (z)', fmt(m.height_z_m), ' m')
        + st('Planform alanı', fmt(m.planform_area_m2), ' m²') + st('Ön alan', fmt(m.frontal_area_m2), ' m²') + st('Islak alan', fmt(m.wetted_area_m2), ' m²')
        + st('Hacim', m.closed ? fmt(m.volume_m3) : '–', ' m³') + st('Üçgen', m.n_triangles.toLocaleString('tr-TR'));
      $('#warn').innerHTML = (m.closed ? '<div class="alert good">✓ Yüzey kapalı (su geçirmez)</div>' : '') + r.warnings.map((w) => `<div class="alert warn">⚠︎ ${esc(w)}</div>`).join('');
      draw3D($('#view'), r);
      $('#to-cfd').disabled = false;
    } catch (err) { $('#warn').innerHTML = `<div class="alert bad">${esc(err.message)}</div>`; }
  }
  const onTf = debounce(() => { readForm(); prepare(true); }, 300);
  ['#unit', '#fwd', '#up', '#rx', '#ry', '#rz', '#center', '#sym'].forEach((s) => { $(s).addEventListener('change', onTf); if ($(s).type === 'number') $(s).addEventListener('input', onTf); });
  const onRef = () => {
    const e = ext(); if (!e) return;
    e.aref = +$('#aref').value; e.lref = +$('#lref').value; e.cofr = [+$('#cx').value, +$('#cy').value, +$('#cz').value]; saveState();
  };
  ['#aref', '#lref', '#cx', '#cy', '#cz'].forEach((s) => { $(s).oninput = onRef; });
  $$('[data-ref]').forEach((b) => { b.onclick = () => { if (!lastMetrics) return; $('#aref').value = +lastMetrics[b.dataset.ref].toPrecision(5); onRef(); }; });
  $('#to-cfd').onclick = () => { onRef(); state.cfdSource = 'external'; saveState(); location.hash = '#/cfd'; };

  if (ext()) { fillForm(); prepare(false); }
}
