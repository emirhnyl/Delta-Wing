# Delta Kanat 3B CFD Analiz ve Optimizasyon Aracı

Parametrik bir **delta kanat + airfoil** geometrisi üretir, **OpenFOAM ile 3B RANS CFD**
analizi yapar ve kanadın **kaldırma (lift)** ve **sürükleme (drag)** kuvvetlerini hesaplar.
Planform ve profil parametreleri bir YAML dosyasından değiştirilir ve dahili optimizasyon
döngüsüyle otomatik olarak optimize edilir.

```
YAML konfig ──► parametrik geometri ──► STL (su geçirmez)
                    │                        │
                    │                        ▼
                    │        blockMesh + snappyHexMesh ──► simpleFoam (k-ω SST)
                    │                                              │
                    ▼                                              ▼
        Hızlı çözücü (VLM + Polhamus)                 Kaldırma / sürükleme [N], CL, CD
                    │                                              ▲
                    └──────► Optimizasyon (DE / Nelder-Mead) ──────┘ (--verify)
```

## Kurulum

```bash
git clone https://github.com/emirhnyl/Delta-Wing.git
cd Delta-Wing
pip install -r requirements.txt
```

3B CFD için OpenFOAM gerekir (openfoam.com / ESI sürümleri önerilir, v1912+):

- Ubuntu: `sudo apt install openfoam` (ya da openfoam.com'un kendi paketleri, örn. `openfoam2312`)
- Araç OpenFOAM ortam dosyasını (`etc/bashrc`) kendisi bulur; bulamazsa
  `openfoam.bashrc` alanına yolunu yazın.

> **Not:** Ubuntu 24.04'ün `openfoam` (1912) paketinde tüm fonksiyon nesneleri
> `error in IOstream "sha1"` hatasıyla çöküyor. Araç bu paketi algılayınca
> `forceCoeffs` yerine kuvvetleri doğrudan çözüm alanlarından (basınç + duvar kayma
> gerilmesi) Python'da integre eder (`deltawing/foamforces.py`). Diğer sürümlerde
> `forceCoeffs` kullanılır, iterasyon geçmişi `postProcessing/` klasörüne yazılır.

## Hızlı başlangıç

```bash
# 1) Geometriyi üret ve incele (STL + görsel + özet)
python run.py geometry -c config/delta_wing.yaml

# 2) Saniyeler içinde ön analiz (VLM + Polhamus girdap kaldırması + viskoz direnç)
python run.py quick -c config/delta_wing.yaml

# 3) 3B RANS CFD: her hücum açısı için ayrı OpenFOAM vakası kurar, çalıştırır, sonuçları toplar
python run.py cfd -c config/delta_wing.yaml --alpha 4 8 12

# 4) Optimizasyon (hızlı çözücüyle) + en iyi tasarımı 3B CFD ile doğrula
python run.py optimize -c config/optimize_vlm.yaml --verify
```

Herhangi bir parametreyi dosyayı değiştirmeden geçersiz kılabilirsiniz:

```bash
python run.py cfd --alpha 10 --set wing.le_sweep_deg=65 --set airfoil.root.thickness=0.06
```

Vakaları başka bir makinede/kümede çalıştırmak için:

```bash
python run.py cfd --no-run --case-dir runs/cfd/kume   # yalnızca vaka dosyalarını yaz
# ... her runs/cfd/kume/alpha_*/ klasöründe ./Allrun çalıştırın ...
python run.py collect --case-dir runs/cfd/kume        # sonuçları topla, grafik çiz
```

## Çıktılar

| Dosya | İçerik |
|---|---|
| `runs/<konfig>/geometry.png`, `wing_half.stl`, `wing_full.stl` | Planform, kesitler, 3B görünüm, geometri tablosu, STL |
| `runs/<konfig>/quick_polar.{csv,json,png}` | Hızlı çözücü polarları |
| `runs/<konfig>/cfd_results.{csv,json}` | **CFD: CL, CD, kaldırma [N], sürükleme [N], L/D**, basınç/viskoz ayrımı |
| `runs/<konfig>/cfd_polar.png` | CFD ve hızlı model karşılaştırması |
| `runs/cfd/.../alpha_*/` | Tam OpenFOAM vakası (ParaView ile `case.foam` açılabilir) |
| `runs/optimization/<ad>/` | `history.csv`, `best_config.yaml`, `best_summary.json`, `convergence.png`, `best_geometry.png` |

Kuvvetler **tam kanat** içindir: CFD yarı model (y = 0 simetri düzlemi) üzerinde çözülür,
sonuçlar ikiyle çarpılır. Kaldırma serbest akışa dik, sürükleme serbest akışa paraleldir:

```
L = CL · ½ρV² · S        D = CD · ½ρV² · S        S = planform alanı (tam kanat)
```

## Geometri parametreleri (`wing`, `airfoil`)

| Parametre | Anlamı |
|---|---|
| `root_chord` | Kök veteri [m] |
| `span` | Uçtan uca açıklık [m] |
| `le_sweep_deg` | Hücum kenarı ok açısı [°] |
| `taper_ratio` | Uç veteri / kök veteri (saf delta = 0; ağ için min. `min_tip_chord_ratio`) |
| `twist_tip_deg` | Uçta burulma, negatif = washout (kökten uca doğrusal) |
| `dihedral_deg` | Dihedral açısı |
| `airfoil.root`, `airfoil.tip` | Kök ve uç profili; ara kesitler karıştırılır |

Airfoil tipleri:

```yaml
root: {type: naca4, code: "0008"}                                  # klasik kod
root: {type: naca4, camber: 0.02, camber_pos: 0.4, thickness: 0.07} # sürekli (optimizasyon için)
root: {type: cst, upper: [0.12, 0.10, 0.10, 0.08], lower: [-0.12, -0.10, -0.08, -0.06]}
root: {type: file, path: profiles/benim_profilim.dat}               # Selig formatı
```

CST ağırlıkları da optimize edilebilir: `airfoil.root.upper.0: [0.05, 0.25]`.

## Optimizasyon (`optimization`)

```yaml
optimization:
  fidelity: vlm            # vlm: hızlı çözücü | openfoam: her değerlendirme tam 3B CFD
  method: differential_evolution   # veya nelder-mead, powell
  mode: fixed_lift         # fixed_alpha: sabit açı | fixed_lift: hedef kaldırmayı veren açıda
  lift_target_N: 300
  alpha_deg: 8
  objective: min_drag      # max_LD | min_drag | max_lift | min_CD | max_CL
  max_evals: 600
  constraints:             # ceza fonksiyonu ile
    min_volume_m3: 0.018   # min_lift_N, max_drag_N, min_CL, min_LD, min/max_area_m2,
    max_span_m: 1.5        # max_span_m, min_root_thickness
  variables:               # konfigürasyondaki herhangi bir sayısal alan: [alt, üst]
    wing.span: [0.9, 1.5]
    wing.le_sweep_deg: [50, 70]
    airfoil.root.thickness: [0.05, 0.12]
```

Önerilen iş akışı: geniş tasarım uzayını `fidelity: vlm` ile (dakikalar) tarayın,
`--verify` ile en iyi tasarımı 3B CFD'de doğrulayın; gerekirse en iyi tasarım etrafında
dar sınırlarla `fidelity: openfoam` (bkz. `config/optimize_cfd.yaml`) çalıştırın.

## CFD kurulumu (`openfoam`)

- **Ağ:** dikdörtgen alan (5c önde, 10c arkada, 5c yanda/dikeyde), blockMesh arka plan +
  snappyHexMesh; kanat yüzeyi `surface_level`, keskin kenarlar `feature_level`, kanat
  çevresi ve iz bölgesi için rafinasyon kutuları. İsteğe bağlı sınır tabakası: `layers.enabled`.
- **Sınır koşulları:** dış yüzeylerde `freestreamVelocity`/`freestreamPressure`
  (hücum açısı hız vektörüyle verilir, geometri döndürülmez), kanatta duvar
  (`nutUSpaldingWallFunction`, y⁺'dan bağımsız), y = 0'da `symmetryPlane`.
- **Çözücü:** `simpleFoam`, k-ω SST, ikinci mertebe (linearUpwind) momentum.
- **Paralel:** `n_procs` > 1 ise `hierarchical` ayrıştırma ve `mpirun`.

**Ağ bağımsızlığı:** Güvenilir mutlak değerler için `base_cell_size`, `surface_level`
ve `feature_level` değerlerini adım adım artırıp CL/CD değişimi %1-2'nin altına inene
kadar tekrarlayın. Sürükleme (özellikle sürtünme bileşeni) ağa kaldırmadan daha
duyarlıdır; y⁺ ≈ 1 hedefleniyorsa `layers.enabled: true` kullanın.

## Örnek sonuç: temel tasarım (`config/delta_wing.yaml`)

60° ok açılı, 1.2 m açıklıklı, NACA 0008→0006 profilli delta kanat, V = 50 m/s,
~158 bin hücre (`surface_level: [4,5]`, `feature_level: 5`), 1000 iterasyon, 4 çekirdek
(açı başına ~5 dk). Ağ bağımsızlık çalışması yapılmamış, orta çözünürlüklü bir örnektir.

| α [°] | CL (CFD) | CD (CFD) | Kaldırma [N] | Sürükleme [N] | L/D | CL / CD (hızlı çözücü) |
|---:|---:|---:|---:|---:|---:|---:|
| 4  | 0.172 | 0.0163 | 165.5 | 15.7 | 10.5 | 0.188 / 0.0206 |
| 8  | 0.362 | 0.0449 | 349.4 | 43.3 | 8.07 | 0.400 / 0.0637 |
| 12 | 0.532 | 0.0961 | 513.2 | 92.7 | 5.54 | 0.631 / 0.1415 |

Hızlı çözücü kaldırmayı %10-19, sürüklemeyi daha fazla yüksek tahmin eder: Polhamus
modeli hücum kenarı emmesinin tamamen kaybolduğunu varsayar, oysa yuvarlak burunlu
NACA profilleri emmenin bir kısmını korur. Eğilimler uyumludur; mutlak değerler için CFD esastır.

## Hızlı çözücünün modeli ve sınırları

- **VLM:** kamber yüzeyinde at nalı girdap kafesi; Bertin & Smith Örnek 7.2 ile birebir
  doğrulanmıştır (testler). Delta kanatlar için kaldırma eğimi DATCOM ile %5 içinde.
- **Polhamus analojisi:** ok açısı ≥ 45° olduğunda hücum kenarı girdabı kaldırması
  (`CL = Kp sinα cos²α + Kv sin²α cosα`) ve emme kaybı (`CDi = CL tanα`). Girdap
  patlaması ve stall modellenmez (~20°'nin üzeri güvenilmez). Keskin hücum kenarı
  varsayar; yuvarlak burunlu kalın profillerde girdap kaldırmasını abartabilir.
- **Viskoz direnç:** türbülanslı düz levha Cf + Raymer form faktörü.

Hızlı çözücü tasarım uzayını taramak ve *eğilimleri* bulmak içindir; nihai kaldırma ve
sürükleme değerleri için 3B CFD sonuçlarını kullanın.

## Testler

```bash
python -m pytest tests -q
```

## Dosya yapısı

```
Delta-Wing/
├── run.py                  # komut satırı arayüzü
├── config/                 # örnek konfigürasyonlar
├── deltawing/
│   ├── airfoil.py          # NACA 4, CST, .dat profilleri
│   ├── geometry.py         # delta kanat planformu, 3B yüzey, STL
│   ├── vlm.py              # VLM + Polhamus + viskoz direnç (hızlı çözücü)
│   ├── openfoam.py         # OpenFOAM vaka üretimi, çalıştırma, sonuç okuma
│   ├── foamforces.py       # alanlardan kuvvet integrasyonu (yedek / doğrulama)
│   ├── optimize.py         # optimizasyon döngüsü
│   ├── plots.py            # grafikler
│   └── config.py           # varsayılanlar, YAML yükleme
└── tests/
```
