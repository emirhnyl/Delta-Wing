import { $, api, state, toast, esc, icon, applyTheme, refreshEnv } from '../core.js';

const IMAGES = ['opencfd/openfoam-default:2512', 'opencfd/openfoam-default:2506', 'opencfd/openfoam-default:2412', 'opencfd/openfoam-default:2406', 'opencfd/openfoam-default:latest'];

export async function render(main) {
  const s = await api.get('/api/settings');
  main.innerHTML = `
  <div class="page-head"><div><h1>Ayarlar</h1><p>Uygulama genelindeki tercihler <span class="mono small">data/settings.json</span> dosyasında saklanır.</p></div></div>
  <div class="grid g2">
    <div class="card"><h2>CFD çalıştırıcı</h2>
      <div class="field"><label>OpenFOAM nerede çalışsın?</label><select id="runner">
        <option value="auto">Otomatik (yerel varsa yerel, yoksa Docker)</option><option value="docker">Her zaman Docker</option><option value="local">Her zaman yerel kurulum</option></select></div>
      <div class="field"><label>Docker imajı</label><select id="image">${IMAGES.map((i) => `<option>${i}</option>`).join('')}<option value="__custom">Özel…</option></select>
        <input type="text" id="image-c" class="hidden" style="margin-top:6px" placeholder="kullanıcı/imaj:etiket"><div class="help">opencfd/openfoam-default 2306 ve sonrası Apple Silicon (arm64) için yereldir. İmajı değiştirince Kurulum'dan indirin.</div></div>
      <div class="field"><label>Varsayılan paralel çekirdek sayısı</label><input type="number" id="nprocs" min="1" max="128" value="${s.n_procs}"></div>
    </div>
    <div class="card"><h2>Görünüm</h2>
      <div class="field"><label>Tema</label><div class="seg" id="theme">${[['auto', 'Sistem'], ['light', 'Açık'], ['dark', 'Koyu']].map(([k, l]) => `<button data-t="${k}" class="${s.theme === k ? 'on' : ''}">${l}</button>`).join('')}</div></div>
      <h3>Hakkında</h3>
      <p class="small muted">Delta-Wing CFD Studio ${esc(state.meta.version)} · Geometri: parametrik planform + NACA/CST/dosya profilleri · Hızlı çözücü: VLM + Polhamus · CFD: OpenFOAM simpleFoam k-ω SST.</p>
    </div>
  </div>
  <div class="actions" style="margin-top:16px"><button class="btn primary" id="save">${icon('save')} Kaydet</button></div>`;
  $('#runner').value = s.runner;
  if (IMAGES.includes(s.docker_image)) $('#image').value = s.docker_image; else { $('#image').value = '__custom'; $('#image-c').classList.remove('hidden'); $('#image-c').value = s.docker_image; }
  $('#image').onchange = (e) => $('#image-c').classList.toggle('hidden', e.target.value !== '__custom');
  let theme = s.theme;
  $('#theme').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; theme = b.dataset.t; [...$('#theme').children].forEach((x) => x.classList.toggle('on', x === b)); applyTheme(theme); };
  $('#save').onclick = async () => {
    const img = $('#image').value === '__custom' ? $('#image-c').value.trim() : $('#image').value;
    const out = await api.post('/api/settings', { runner: $('#runner').value, docker_image: img, n_procs: +$('#nprocs').value, theme });
    state.meta.settings = out;
    toast('Ayarlar kaydedildi', 'good');
    refreshEnv();
  };
}
