import { $, api, state, toast, fmt, esc, icon, mountJob, stopMonitor, refreshEnv } from '../core.js';

export async function render(main) {
  main.innerHTML = `
  <div class="page-head">
    <div><h1>Kurulum</h1><p>3B CFD için OpenFOAM gerekir. macOS'ta en kolay yol: Homebrew → Colima (yönetici şifresi gerektirmeyen hafif Docker motoru) → OpenFOAM Docker imajı (Apple Silicon için yerel arm64). Adımları tek tek veya tek tıkla otomatik çalıştırabilirsiniz.</p></div>
    <div class="actions"><button class="btn" id="refresh">${icon('refresh')} Durumu yenile</button><button class="btn primary" id="auto">${icon('play')} Tümünü otomatik kur</button></div>
  </div>
  <div class="split-wide">
    <div class="grid">
      <div class="card"><h2>Kurulum adımları</h2><div class="steps" id="steps"><div class="empty"><span class="spinner"></span>Kontrol ediliyor…</div></div></div>
      <div class="card"><h2>Docker sanal makine kaynakları <span class="sub">Colima</span></h2>
        <div id="res"></div>
        <div class="help small muted">CFD çekirdek sayısı bu CPU değerine eşitlenir. MacBook Air'de 4-6 CPU ve 6-8 GB RAM iyi bir başlangıçtır; değişiklik için “Docker VM'i başlat”a tekrar basın.</div>
      </div>
    </div>
    <div class="grid">
      <div class="card"><h2>Sistem</h2><div id="sys"></div></div>
      <div id="job"></div>
      <div class="card"><h2>Notlar</h2>
        <ul class="small muted" style="margin:0;padding-left:18px;line-height:1.7">
          <li>Homebrew kurulumu yönetici şifresi ister; bu yüzden bir Terminal penceresinde açılır. Kurulum bitince bu sayfada <b>Durumu yenile</b>'ye basın.</li>
          <li>Docker Desktop zaten kuruluysa Colima gerekmez; “Docker Desktop'ı aç” ile motoru başlatın.</li>
          <li>OpenFOAM imajı ~400 MB indirir (açılınca ~2 GB). Tek seferliktir.</li>
          <li>Proje klasörü ev dizininizin (<span class="mono">~</span>) altında olmalı; Colima yalnızca bu dizini kapsayıcıya bağlar.</li>
          <li>Mac yeniden başlatıldıktan sonra CFD'den önce <b>Docker VM'i başlat</b> adımını tekrar çalıştırın (veya Terminal'de <span class="mono">colima start</span>).</li>
        </ul></div>
    </div>
  </div>`;
  $('#refresh').onclick = () => load();
  $('#auto').onclick = () => act('auto');
  if (state.activeJobs.setup) watch(state.activeJobs.setup);
  load();
}

let res = null;

async function load() {
  const s = await refreshEnv();
  if (!s) return;
  const sys = s.system;
  res = res || { ...(state.meta.settings.colima || s.recommended) };
  $('#sys').innerHTML = `<table class="tbl"><tbody>
    <tr><td>İşletim sistemi</td><td>${sys.os === 'Darwin' ? `macOS ${esc(sys.mac_version || '')}` : esc(sys.os)} · ${esc(sys.machine)}${sys.machine === 'arm64' ? ' (Apple Silicon)' : ''}</td></tr>
    <tr><td>İşlemci / bellek</td><td>${sys.cpus} çekirdek · ${fmt(sys.memory_gb, 3)} GB</td></tr>
    <tr><td>Python</td><td>${esc(sys.python)}</td></tr>
    <tr><td>Aktif çalıştırıcı</td><td>${s.runner === 'docker' ? `<span class="badge good">Docker</span> ${esc(s.image.name)}` : s.runner === 'local' ? `<span class="badge good">Yerel OpenFOAM</span> <span class="mono small">${esc(s.local_openfoam.bashrc || '')}</span>` : '<span class="badge warn">Yok</span>'}</td></tr>
    <tr><td>Docker motoru</td><td>${s.docker_engine.ok ? esc(s.docker_engine.detail) : '–'}</td></tr></tbody></table>`;

  const mac = s.is_mac;
  const step = (n, ok, title, desc, btns, optional) => `<div class="step ${ok ? 'done' : optional ? '' : 'todo'}"><div class="num">${ok ? '✓' : n}</div>
      <div><b>${title}</b><div class="desc">${desc}</div></div><div class="actions">${btns}</div></div>`;
  const B = (a, l, primary) => `<button class="btn sm ${primary ? 'primary' : ''}" data-act="${a}">${l}</button>`;
  let html = '';
  if (s.local_openfoam.ok) {
    html += `<div class="alert good">✓ Bu bilgisayarda yerel OpenFOAM bulundu (${esc(s.local_openfoam.bashrc || 'PATH')}). Docker kurmadan CFD çalıştırabilirsiniz; Ayarlar'dan çalıştırıcıyı değiştirebilirsiniz.</div>`;
  }
  if (mac) {
    html += step(1, s.homebrew.ok, 'Homebrew', s.homebrew.ok ? esc(s.homebrew.path) : 'macOS paket yöneticisi. Terminal açılır ve şifreniz istenir.', s.homebrew.ok ? '' : B('install_homebrew', 'Homebrew’u kur', true));
    const engineOk = s.colima.ok || s.docker_desktop.ok;
    html += step(2, engineOk && s.docker_cli.ok, 'Docker motoru + CLI', s.colima.ok ? 'Colima kurulu' : s.docker_desktop.ok ? 'Docker Desktop kurulu' : 'Colima + docker CLI (brew install colima docker)',
      (engineOk && s.docker_cli.ok) ? '' : B('install_colima', 'Colima’yı kur', true) + (s.docker_desktop.ok ? B('open_docker_desktop', 'Docker Desktop’ı aç') : ''));
    html += step(3, s.docker_engine.ok, 'Docker sanal makinesi çalışıyor', s.docker_engine.ok ? esc(s.docker_engine.detail) : 'Colima VM’i seçilen CPU/RAM ile başlatılır (ilk sefer 1-3 dk).',
      (s.colima.ok ? B('start_colima', s.docker_engine.ok ? 'Yeniden başlat' : 'Docker VM’i başlat', !s.docker_engine.ok) + (s.colima.running ? B('stop_colima', 'Durdur') : '') : '') + (s.docker_desktop.ok && !s.colima.ok ? B('open_docker_desktop', 'Docker Desktop’ı aç', true) : ''));
  } else {
    html += step(1, s.docker_cli.ok, 'Docker CLI', s.docker_cli.ok ? esc(s.docker_cli.path) : 'Linux: dağıtımınızın docker paketini kurun (ör. sudo apt install docker.io)', '');
    html += step(2, s.docker_engine.ok, 'Docker motoru çalışıyor', s.docker_engine.ok ? esc(s.docker_engine.detail) : 'sudo systemctl start docker', '');
  }
  const n0 = mac ? 4 : 3;
  html += step(n0, s.image.ok, 'OpenFOAM Docker imajı', `${esc(s.image.name)}${s.image.ok ? ' indirildi' : ' · ~400 MB indirme'}`, s.docker_engine.ok && !s.image.ok ? B('pull_image', 'İmajı indir', true) : (s.image.ok ? B('pull_image', 'Güncelle') : ''));
  html += step(n0 + 1, false, 'Test çalıştırması', 'Çok kaba ağla kısa bir CFD (1-2 dk): ağ üretimi, çözücü ve kuvvet hesabının uçtan uca çalıştığını doğrular.', s.ready ? B('test_openfoam', 'Testi çalıştır', true) : '', true);
  $('#steps').innerHTML = html;
  $('#steps').onclick = (e) => { const b = e.target.closest('[data-act]'); if (b) act(b.dataset.act); };

  const rec = s.recommended;
  $('#res').innerHTML = `<div class="grid g3">
    <div class="field"><label>CPU <span class="muted">önerilen ${rec.cpu}</span></label><input type="number" id="r-cpu" min="1" max="${sys.cpus}" value="${res.cpu}"></div>
    <div class="field"><label>RAM <span class="muted">GB · önerilen ${rec.memory}</span></label><input type="number" id="r-mem" min="2" max="${Math.floor(sys.memory_gb || 64)}" value="${res.memory}"></div>
    <div class="field"><label>Disk <span class="muted">GB</span></label><input type="number" id="r-disk" min="20" max="500" value="${res.disk ?? 40}"></div></div>`;
  ['cpu', 'mem', 'disk'].forEach((k) => { $(`#r-${k}`).oninput = (e) => { res[{ cpu: 'cpu', mem: 'memory', disk: 'disk' }[k]] = +e.target.value; }; });
}

async function act(action) {
  if (action === 'test_openfoam' && !confirm('Kısa bir test CFD çalıştırılsın mı? (1-2 dk)')) return;
  try {
    const r = await api.post('/api/setup/action', { action, resources: res, image: state.meta.settings.docker_image });
    state.activeJobs.setup = r.job_id;
    watch(r.job_id);
  } catch (e) { toast(e.message, 'bad'); }
}

function watch(id) {
  mountJob($('#job'), id, {
    openLog: true,
    onDone: async (j) => {
      if (j.status === 'done') toast(typeof j.result === 'string' ? j.result : `${j.title}: tamamlandı`, 'good');
      state.meta = await api.get('/api/meta');
      load();
    },
    onLost: () => { delete state.activeJobs.setup; },
  });
}

export function leave() { stopMonitor($('#job')); }
