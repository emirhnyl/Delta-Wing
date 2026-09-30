import { $, api, esc, fmt, fmtDate, empty, icon } from '../core.js';
import { mountFlowViz } from '../flowviz.js';

let selected = null;

export async function render(main) {
  main.innerHTML = `
  <div class="page-head"><div><h1>Akış Görselleştirme</h1><p>CFD çözümlerinden yüzey basınç dağılımı, 3B akış çizgileri ve kesit haritaları (hız, basınç, toplam basınç). Görüntüler PNG olarak indirilebilir ve rapora eklenir.</p></div></div>
  <div class="card" style="margin-bottom:16px"><div class="field" style="margin:0"><label>CFD çalışması</label><select id="study"></select></div></div>
  <div id="viz"></div>`;
  const list = (await api.get('/api/studies')).filter((s) => (s.kind === 'cfd' || s.kind === 'external') && s.n_results);
  if (!list.length) { $('#viz').innerHTML = `<div class="card">${empty('Henüz tamamlanmış CFD çalışması yok. <a href="#/cfd">3B CFD Analizi</a> sayfasından bir analiz çalıştırın.', 'cfd')}</div>`; $('#study').closest('.card').classList.add('hidden'); return; }
  if (!list.some((s) => s.id === selected)) selected = list[0].id;
  $('#study').innerHTML = list.map((s) => `<option value="${esc(s.id)}" ${s.id === selected ? 'selected' : ''}>${esc(s.name)} · ${fmtDate(s.created)} · ${s.n_results} açı${s.best ? ` · L/D ${fmt(s.best.L_over_D, 3)}` : ''}</option>`).join('');
  $('#study').onchange = (e) => { selected = e.target.value; mountFlowViz($('#viz'), selected); };
  mountFlowViz($('#viz'), selected);
}

export function select(id) { selected = id; }
