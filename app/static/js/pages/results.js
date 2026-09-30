import { mountFlowViz } from '../flowviz.js';
import { $, $$, api, state, saveState, closeModal, toast, fmt, esc, icon, fmtDate, polarCharts, resultsTable, modal, empty } from '../core.js';

export const KIND = { quick: ['Hızlı', 'info'], cfd: ['CFD', 'good'], external: ['Harici CFD', 'good'], optimization: ['Optimizasyon', 'warn'] };

export async function render(main) {
  main.innerHTML = `
  <div class="page-head">
    <div><h1>Sonuçlar</h1><p>Tüm analiz çalışmaları <span class="mono small">runs/studies/</span> altında saklanır. Birden fazla çalışmayı seçip polarlarını karşılaştırabilir, rapor ve ZIP indirebilirsiniz.</p></div>
    <div class="actions"><button class="btn" id="cmp" disabled>${icon('chart')} Seçilenleri karşılaştır</button><button class="btn" id="ref">${icon('refresh')} Yenile</button></div>
  </div>
  <div class="card flush" id="list"></div>
  <div id="compare" style="margin-top:16px"></div>`;
  const load = async () => {
    const list = await api.get('/api/studies');
    if (!list.length) { $('#list').innerHTML = empty('Henüz çalışma yok. Hızlı analiz, CFD veya optimizasyon çalıştırın.', 'results'); return; }
    $('#list').innerHTML = `<div class="tbl-wrap" style="border:0;max-height:none"><table class="tbl"><thead><tr><th style="width:30px"></th><th>Ad</th><th>Tür</th><th>Tarih</th><th>Durum</th><th class="num">Açı</th><th class="num">En iyi L/D</th><th class="num">CL / CD @ en iyi</th><th class="num">Alan [m²]</th><th></th></tr></thead><tbody>
      ${list.map((s) => {
        const [k, c] = KIND[s.kind] || [s.kind, ''];
        const st = s.status === 'done' ? '<span class="badge good">Tamam</span>' : s.status === 'running' ? '<span class="badge info">Sürüyor</span>' : s.status === 'cancelled' ? '<span class="badge warn">İptal</span>' : s.status === 'failed' ? '<span class="badge bad">Hata</span>' : `<span class="badge">${esc(s.status || '')}</span>`;
        return `<tr class="click" data-id="${esc(s.id)}"><td><input type="checkbox" data-sel="${esc(s.id)}" ${s.n_results ? '' : 'disabled'}></td><td><b>${esc(s.name)}</b><div class="small muted mono">${esc(s.id)}</div></td>
          <td><span class="badge ${c}">${k}</span></td><td>${fmtDate(s.created)}</td><td>${st}</td><td class="num">${s.n_results}</td>
          <td class="num">${fmt(s.best?.L_over_D, 3)}</td><td class="num">${s.best ? `${fmt(s.best.CL)} / ${fmt(s.best.CD)}` : '–'}</td><td class="num">${fmt(s.geometry?.area_m2)}</td>
          <td class="num" style="white-space:nowrap"><a class="btn sm ghost" href="/api/studies/${esc(s.id)}/report" target="_blank" title="Rapor">${icon('report')}</a><a class="btn sm ghost" href="/api/studies/${esc(s.id)}/download" title="ZIP">${icon('download')}</a><button class="btn sm ghost danger" data-del="${esc(s.id)}" title="Sil">${icon('trash')}</button></td></tr>`;
      }).join('')}</tbody></table></div>`;
  };
  $('#list').onclick = async (e) => {
    if (e.target.closest('a, input')) { if (e.target.dataset.sel !== undefined) $('#cmp').disabled = $$('[data-sel]:checked').length < 1; return; }
    const d = e.target.closest('[data-del]');
    if (d) { if (confirm('Çalışma ve tüm CFD vakaları silinsin mi?')) { await api.del(`/api/studies/${d.dataset.del}`); toast('Silindi'); load(); } return; }
    const tr = e.target.closest('tr[data-id]');
    if (tr) openStudy(tr.dataset.id);
  };
  $('#ref').onclick = load;
  $('#cmp').onclick = async () => {
    const ids = $$('[data-sel]:checked').map((c) => c.dataset.sel);
    const ss = await Promise.all(ids.map((id) => api.get(`/api/studies/${id}`)));
    $('#compare').innerHTML = '<div id="cmp-charts"></div>';
    polarCharts($('#cmp-charts'), ss.filter((s) => s.results?.length).map((s) => ({ name: s.name, rows: s.results })));
    $('#compare').scrollIntoView({ behavior: 'smooth' });
  };
  load();
}

export async function openStudy(id) {
  const s = await api.get(`/api/studies/${id}`);
  const [k] = KIND[s.kind] || [s.kind];
  const geo = s.geometry || {};
  const body = modal(`<div class="page-head" style="margin-bottom:12px"><div><h1 style="font-size:19px">${esc(s.name)}</h1><p>${k} · ${fmtDate(s.created)} · ${esc(s.runner || '')}</p></div>
    <div class="actions"><a class="btn sm" href="/api/studies/${id}/report" target="_blank">${icon('report')} Rapor</a><a class="btn sm" href="/api/studies/${id}/download">${icon('download')} ZIP</a>
    ${s.kind === 'optimization' && s.best_config ? `<button class="btn sm primary" data-apply>${icon('check')} Tasarıma uygula</button>` : ''}
    ${s.config?.wing && s.kind !== 'external' ? `<button class="btn sm" data-load>${icon('design')} Tasarımı yükle</button>` : ''}
    <button class="btn sm" data-close-modal>Kapat</button></div></div>
    <div class="stats" style="margin-bottom:12px">${Object.entries({ 'Alan': [geo.area_m2, 'm²'], 'Açıklık': [geo.span_m ?? geo.span_y_m, 'm'], 'AR': [geo.aspect_ratio, ''], 'OAV / ref.': [geo.mac_m ?? geo.lref_m, 'm'], 'Hız': [s.flow?.velocity, 'm/s'], 'Re': [s.flow?.reynolds ? s.flow.reynolds / 1e6 : null, '×10⁶'] })
      .filter(([, [v]]) => v !== undefined && v !== null).map(([kk, [v, u]]) => `<div class="stat"><div class="k">${kk}</div><div class="v">${fmt(v)}<span class="u"> ${u}</span></div></div>`).join('')}</div>
    ${s.results?.length ? resultsTable(s.results, [['drag_pressure_N', 'Basınç sürük. [N]'], ['drag_viscous_N', 'Sürtünme sürük. [N]'], ['cells', 'Hücre'], ['converged_hint', 'Yakınsama']]) : '<div class="alert warn">Bu çalışmada sonuç yok (iptal edilmiş veya hata almış olabilir).</div>'}
    <div id="m-charts" style="margin-top:14px"></div>
    ${s.kind === 'cfd' || s.kind === 'external' ? '<div id="m-flow" style="margin-top:14px"></div>' : ''}
    <div class="grid g2" style="margin-top:14px">${['geometry.png', 'polar.png'].map((f) => `<img src="/api/studies/${id}/file/${f}" onerror="this.remove()" style="width:100%;border:1px solid var(--border);border-radius:8px">`).join('')}</div>`);
  if (s.results?.length > 1) {
    const series = [{ name: s.kind === 'quick' ? 'VLM + Polhamus' : 'OpenFOAM RANS', rows: s.results }];
    if (s.comparison) series.push({ name: 'VLM + Polhamus', rows: s.comparison, dash: true });
    polarCharts(body.querySelector('#m-charts'), series);
  }
  if (body.querySelector('#m-flow')) mountFlowViz(body.querySelector('#m-flow'), id);
  body.querySelector('[data-apply]')?.addEventListener('click', () => { state.cfg.wing = s.best_config.wing; state.cfg.airfoil = s.best_config.airfoil; saveCfg(); toast('Tasarım uygulandı', 'good'); location.hash = '#/tasarim'; });
  body.querySelector('[data-load]')?.addEventListener('click', () => { state.cfg.wing = s.config.wing; state.cfg.airfoil = s.config.airfoil; if (s.config.flow) state.cfg.flow = s.config.flow; saveCfg(); toast('Tasarım yüklendi', 'good'); location.hash = '#/tasarim'; });
}

function saveCfg() { saveState(); closeModal(); }
