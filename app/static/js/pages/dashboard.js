import { $, api, state, fmt, esc, icon, fmtDate, fmtTime, refreshEnv, empty } from '../core.js';
import { TYPE_ICONS } from './design.js';
import { KIND, openStudy } from './results.js';

export async function render(main) {
  const w = state.cfg.wing;
  const t = state.meta.wing_types[w.type];
  main.innerHTML = `
  <div class="page-head"><div><h1>Panel</h1><p>Kanat tasarlayın, saniyeler içinde ön analiz yapın, 3B CFD ile kaldırma ve sürükleme kuvvetlerini hesaplayın ve tasarımı otomatik optimize edin.</p></div></div>
  <div class="grid g3" style="margin-bottom:16px">
    <div class="card"><h2>Aktif tasarım <a class="btn sm" href="#/tasarim">Düzenle</a></h2>
      <div class="row" style="align-items:center"><svg viewBox="0 0 60 40" style="flex:0 0 90px;height:60px"><path d="${TYPE_ICONS[w.type]}" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="1.5"/></svg>
      <div><b>${esc(t.label)}</b><div class="small muted" id="d-sum">…</div></div></div></div>
    <div class="card"><h2>CFD ortamı <a class="btn sm" href="#/kurulum">Kurulum</a></h2><div id="d-env" class="small muted">Kontrol ediliyor…</div></div>
    <div class="card"><h2>Hızlı başlangıç</h2>
      <ol class="small" style="margin:0;padding-left:18px;line-height:1.9">
        <li><a href="#/kurulum">Kurulum</a>: Docker + OpenFOAM (tek tık)</li>
        <li><a href="#/tasarim">Kanat tasarımı</a> veya <a href="#/harici">harici STL</a></li>
        <li><a href="#/hizli">Hızlı analiz</a> ile ön değerlendirme</li>
        <li><a href="#/cfd">3B CFD</a> ile kaldırma / sürükleme</li>
        <li><a href="#/optimizasyon">Optimizasyon</a> → CFD ile doğrulama</li>
      </ol></div>
  </div>
  <div class="grid g2">
    <div class="card"><h2>İşler <span class="sub">bu oturum</span></h2><div id="d-jobs"></div></div>
    <div class="card"><h2>Son çalışmalar <a class="btn sm" href="#/sonuclar">Tümü</a></h2><div id="d-studies"></div></div>
  </div>`;

  api.post('/api/geometry/preview', state.cfg).then((r) => {
    const s = r.summary;
    $('#d-sum').innerHTML = `S = ${fmt(s.area_m2)} m² · b = ${fmt(s.span_m)} m · AR = ${fmt(s.aspect_ratio, 3)} · Λ = ${fmt(s.le_sweep_deg, 3)}°<br>V = ${fmt(state.cfg.flow.velocity)} m/s · Re = ${fmt(r.flow.reynolds / 1e6, 3)}×10⁶`;
  }).catch((e) => { $('#d-sum').textContent = e.message; });

  refreshEnv().then((s) => {
    if (!s) return;
    $('#d-env').innerHTML = s.ready
      ? `<span class="badge good">Hazır</span> ${s.runner === 'docker' ? `Docker · ${esc(s.image.name)}` : 'Yerel OpenFOAM'}<div style="margin-top:6px">${esc(s.docker_engine.detail || '')}</div>`
      : `<span class="badge warn">Kurulum gerekli</span><div style="margin-top:6px">Hızlı analiz ve optimizasyon kurulum olmadan çalışır. 3B CFD için <a href="#/kurulum">Kurulum</a> sayfasındaki adımları tamamlayın.</div>`;
  });

  const jobs = await api.get('/api/jobs');
  $('#d-jobs').innerHTML = jobs.length ? `<table class="tbl"><tbody>${jobs.slice(0, 8).map((j) => `<tr><td>${esc(j.title)}<div class="small muted">${esc(j.stage)}</div></td>
      <td class="num" style="width:120px"><div class="progress"><div style="width:${(j.progress * 100).toFixed(0)}%"></div></div></td>
      <td class="num small muted" style="width:90px">${fmtTime(j.elapsed)}</td>
      <td style="width:90px"><span class="badge ${j.status === 'done' ? 'good' : j.status === 'failed' ? 'bad' : j.status === 'running' ? 'info' : ''}">${{ done: 'Tamam', failed: 'Hata', running: 'Çalışıyor', queued: 'Kuyrukta', cancelled: 'İptal' }[j.status]}</span></td></tr>`).join('')}</tbody></table>`
    : empty('Henüz iş yok.', 'cfd');

  const st = await api.get('/api/studies');
  $('#d-studies').innerHTML = st.length ? `<table class="tbl"><tbody>${st.slice(0, 8).map((s) => `<tr class="click" data-id="${esc(s.id)}"><td><b>${esc(s.name)}</b><div class="small muted">${fmtDate(s.created)}</div></td>
      <td><span class="badge ${(KIND[s.kind] || [])[1] || ''}">${(KIND[s.kind] || [s.kind])[0]}</span></td><td class="num">${s.best ? `L/D ${fmt(s.best.L_over_D, 3)}` : '–'}</td></tr>`).join('')}</tbody></table>`
    : empty('Henüz çalışma yok.', 'results');
  $('#d-studies').onclick = (e) => { const tr = e.target.closest('[data-id]'); if (tr) openStudy(tr.dataset.id); };
}
