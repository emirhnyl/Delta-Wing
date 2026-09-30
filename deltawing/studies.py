"""Analiz çalışmaları (study): kayıt, iş fonksiyonları ve HTML rapor.

Her çalışma ``runs/studies/<id>/`` altında ``study.json`` + görseller + (CFD ise)
OpenFOAM vakaları olarak saklanır; arayüz geçmişi buradan okur.
"""

from __future__ import annotations

import base64
import copy
import html
import json
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .atmosphere import apply_atmosphere, flow_numbers
from .config import DEFAULTS
from .external import ExternalTransform, body_from_external, body_from_wing
from .geometry import Wing, write_stl
from .monitor import case_status
from .openfoam import run_openfoam
from .runner import resolve_runner
from .vlm import QuickAero, span_loading

ROOT = Path(__file__).resolve().parents[1]
STUDIES = ROOT / "runs" / "studies"

MESH_PRESETS = {
    "kaba": {"label": "Kaba (hızlı kontrol, ~30-60 bin hücre)", "base_cell_size": 0.5,
             "surface_level": [4, 5], "feature_level": 5, "near_level": 3, "wake_level": 2,
             "iterations": 500},
    "orta": {"label": "Orta (~150-300 bin hücre)", "base_cell_size": 0.25,
             "surface_level": [4, 5], "feature_level": 5, "near_level": 3, "wake_level": 2,
             "iterations": 1000},
    "ince": {"label": "İnce (~1-2 milyon hücre)", "base_cell_size": 0.25,
             "surface_level": [5, 6], "feature_level": 6, "near_level": 4, "wake_level": 3,
             "iterations": 1500},
}


def _slug(s: str) -> str:
    tr = str.maketrans("çğıöşüÇĞİÖŞÜ ", "cgiosuCGIOSU_")
    return re.sub(r"[^A-Za-z0-9_-]", "", (s or "calisma").translate(tr))[:40] or "calisma"


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, float) and not np.isfinite(o):
        return None
    return o


class Study:
    def __init__(self, path: Path, data: dict):
        self.path = Path(path)
        self.data = data

    @classmethod
    def create(cls, kind: str, name: str, cfg: dict, **extra) -> "Study":
        sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "_" + _slug(name)
        path = STUDIES / sid
        path.mkdir(parents=True, exist_ok=True)
        st = cls(path, {"id": sid, "name": name or sid, "kind": kind, "created": time.time(),
                        "status": "running", "config": _jsonable(cfg), "results": [], **extra})
        st.save()
        return st

    @classmethod
    def load(cls, sid: str) -> "Study":
        path = STUDIES / sid
        return cls(path, json.loads((path / "study.json").read_text()))

    def save(self) -> None:
        tmp = self.path / "study.json.tmp"
        tmp.write_text(json.dumps(_jsonable(self.data), indent=1, ensure_ascii=False))
        tmp.replace(self.path / "study.json")


def list_studies() -> list[dict]:
    out = []
    if not STUDIES.exists():
        return out
    for d in sorted(STUDIES.iterdir(), reverse=True):
        f = d / "study.json"
        if not f.exists():
            continue
        try:
            s = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        res = s.get("results") or []
        best = max(res, key=lambda r: r.get("L_over_D") or -1e9) if res else None
        out.append({"id": s["id"], "name": s["name"], "kind": s["kind"], "created": s["created"],
                    "status": s.get("status"), "n_results": len(res),
                    "geometry": s.get("geometry", {}), "best": best,
                    "has_report": (d / "report.html").exists()})
    return out


def delete_study(sid: str) -> None:
    path = (STUDIES / sid).resolve()
    if STUDIES.resolve() in path.parents:
        shutil.rmtree(path)


# --------------------------------------------------------------------------- yardımcılar


def prepare_config(cfg: dict) -> dict:
    c = copy.deepcopy(cfg)
    for k, v in DEFAULTS.items():
        if isinstance(v, dict):
            c.setdefault(k, {})
            for kk, vv in v.items():
                c[k].setdefault(kk, copy.deepcopy(vv))
    apply_atmosphere(c)
    return c


def quick_analysis(cfg: dict, alphas, span_alpha: float | None = None) -> dict:
    cfg = prepare_config(cfg)
    wing = Wing.from_config(cfg)
    qa = QuickAero(wing, cfg)
    rows = [qa.analyze(float(a)).to_dict() for a in alphas]
    sa = float(span_alpha if span_alpha is not None else (alphas[len(alphas) // 2] if len(alphas) else 5.0))
    return {
        "results": rows,
        "span": {"alpha_deg": sa, **span_loading(qa, sa)},
        "model": {"Kp": qa.Kp, "Kv": qa.Kv, "alpha0_deg": float(np.degrees(qa.alpha0)),
                  "Ki": qa.Ki, "vortex_lift": qa.vortex, **qa.visc},
        "geometry": wing.summary(),
        "flow": {**cfg["flow"], **flow_numbers(cfg, wing.mac)},
    }


def _b64(path: Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode() if Path(path).exists() else ""


# --------------------------------------------------------------------------- iş fonksiyonları


def cfd_job(job, cfg: dict, alphas, name: str, source: dict | None = None) -> dict:
    """CFD çalışması: source=None -> tasarlanan kanat, {"external": {...}} -> harici geometri."""
    from .plots import plot_body, plot_geometry

    cfg = prepare_config(cfg)
    alphas = [float(a) for a in alphas]
    ext = (source or {}).get("external")
    if ext:
        t = ExternalTransform.from_dict(ext.get("transform"))
        body, metrics = body_from_external(ext["path"], t, ext.get("aref"), ext.get("lref"),
                                           ext.get("cofr"))
        geo = {"type": "external", "file": Path(ext["path"]).name, "area_m2": body.aref,
               "lref_m": body.lref, "symmetric": body.symmetric, **{k: metrics[k] for k in (
                   "length_x_m", "span_y_m", "height_z_m", "wetted_area_m2", "volume_m3",
                   "planform_area_m2", "frontal_area_m2", "closed", "n_triangles")}}
        kind = "external"
        wing = None
    else:
        wing = Wing.from_config(cfg)
        body = body_from_wing(wing, cfg)
        geo = wing.summary()
        kind = "cfd"
    st = Study.create(kind, name, cfg, geometry=geo, alphas=alphas, source=_jsonable(source or {}),
                      runner=resolve_runner(cfg),
                      flow={**cfg["flow"], **flow_numbers(cfg, body.lref)})
    job.meta["study_id"] = st.data["id"]
    job.log(f"Çalışma: {st.data['id']}  |  çalıştırıcı: {st.data['runner']}")
    if st.data["runner"] == "none":
        raise RuntimeError("OpenFOAM bulunamadı. Kurulum sayfasından Docker + OpenFOAM kurulumunu tamamlayın.")
    # görseller
    try:
        if wing is not None:
            plot_geometry(wing, st.path / "geometry.png")
            v, t = wing.surface_mesh(int(cfg["geometry"]["n_span"]), float(cfg["geometry"]["min_tip_chord_ratio"]), full=True)
            write_stl(st.path / "geometry.stl", v, t, "wing")
        else:
            plot_body(body, st.path / "geometry.png")
            write_stl(st.path / "geometry.stl", body.verts, body.tris, "body")
    except Exception as e:  # noqa: BLE001
        job.log(f"Görsel oluşturulamadı: {e}")

    try:
        return _cfd_loop(job, st, cfg, body, wing, alphas)
    except BaseException as e:
        st.data["status"] = "cancelled" if ("İptal" in str(e) or type(e).__name__ == "Cancelled") else "failed"
        st.data["error"] = str(e)[:2000]
        st.save()
        raise


def _cfd_loop(job, st, cfg, body, wing, alphas) -> dict:
    from .plots import plot_polars

    end_time = int(cfg["openfoam"]["iterations"])
    n = len(alphas)
    results = []
    for i, a in enumerate(alphas):
        job.check_cancel()
        case = st.path / "cases" / (f"alpha_{a:+06.2f}".replace("+", "p").replace("-", "m"))
        stop = threading.Event()

        def monitor(case=case, i=i, a=a):
            while not stop.is_set():
                s = case_status(case, end_time)
                job.set((i + s["fraction"]) / n, f"α = {a:g}° ({i + 1}/{n}) · {s['stage']}", live=s,
                        current_alpha=a)
                stop.wait(2.0)

        th = threading.Thread(target=monitor, daemon=True)
        th.start()
        try:
            res = run_openfoam(body, cfg, a, case, cancel_event=job.cancel_event)
        finally:
            stop.set()
            th.join(timeout=5)
        s = case_status(case, end_time)
        res["cells"] = s.get("cells")
        results.append(res)
        st.data["results"] = results
        st.save()
        job.set(stage=f"α = {a:g}° tamamlandı", results=results, live=s)
        job.log(f"α={a:g}°  CL={res['CL']:.4f}  CD={res['CD']:.5f}  L={res['lift_N']:.2f} N  "
                f"D={res['drag_N']:.3f} N  L/D={res['L_over_D']:.2f}  ({res.get('cells')} hücre)")

    series = {"OpenFOAM RANS": results}
    if wing is not None:
        try:
            q = quick_analysis(cfg, alphas)
            st.data["comparison"] = q["results"]
            series["VLM + Polhamus"] = q["results"]
        except Exception as e:  # noqa: BLE001
            job.log(f"Hızlı model karşılaştırması yapılamadı: {e}")
    if len(results) > 1:
        plot_polars(series, st.path / "polar.png", st.data["name"])
    st.data["status"] = "done"
    st.save()
    write_report(st)
    return {"study_id": st.data["id"], "results": results}


def optimization_job(job, cfg: dict, name: str) -> dict:
    from .optimize import run_optimization
    from .plots import plot_geometry, plot_history

    cfg = prepare_config(cfg)
    st = Study.create("optimization", name, cfg)
    job.meta["study_id"] = st.data["id"]
    cfg["optimization"]["output_dir"] = str(st.path / "optimization")
    hist_small: list[dict] = []

    def cb(rec, best, n, nmax):
        hist_small.append({k: rec.get(k) for k in ("eval", "f", "ok", "CL", "CD", "L_over_D", "drag_N", "lift_N")})
        job.set(n / nmax, f"Değerlendirme {n}/{nmax}", history=hist_small[-2000:],
                best={k: v for k, v in best.items() if k != "history"})

    res = run_optimization(cfg, log=job.log, callback=cb, cancel_event=job.cancel_event)
    od = Path(cfg["optimization"]["output_dir"])
    plot_history(res["history"], od / "convergence.png", str(cfg["optimization"]["objective"]))
    import yaml
    best_cfg = yaml.safe_load((od / "best_config.yaml").read_text())
    try:
        w = Wing.from_config(prepare_config(best_cfg))
        plot_geometry(w, st.path / "geometry.png")
        st.data["geometry"] = w.summary()
    except Exception as e:  # noqa: BLE001
        job.log(f"En iyi geometri çizilemedi: {e}")
    shutil.copy(od / "convergence.png", st.path / "polar.png")
    res.pop("history", None)
    st.data.update({"status": "done", "optimization": res, "best_config": best_cfg,
                    "results": [res["result"]]})
    st.save()
    write_report(st)
    return {"study_id": st.data["id"], **res, "best_config": best_cfg}


def save_quick_study(cfg: dict, name: str, alphas) -> dict:
    from .plots import plot_geometry, plot_polars

    q = quick_analysis(cfg, alphas)
    c = prepare_config(cfg)
    st = Study.create("quick", name, c, geometry=q["geometry"], flow=q["flow"], alphas=list(alphas))
    st.data["results"] = q["results"]
    st.data["model"] = q["model"]
    w = Wing.from_config(c)
    plot_geometry(w, st.path / "geometry.png")
    plot_polars({"VLM + Polhamus": q["results"]}, st.path / "polar.png", st.data["name"])
    st.data["status"] = "done"
    st.save()
    write_report(st)
    return {"study_id": st.data["id"]}


# --------------------------------------------------------------------------- rapor

KIND_LABEL = {"quick": "Hızlı analiz (VLM + Polhamus)", "cfd": "3B RANS CFD (OpenFOAM)",
              "external": "Harici geometri – 3B RANS CFD", "optimization": "Optimizasyon"}

GEO_LABELS = [("type", "Tip", ""), ("file", "Dosya", ""), ("span_m", "Açıklık", "m"),
              ("root_chord_m", "Kök veteri", "m"), ("tip_chord_m", "Uç veteri", "m"),
              ("area_m2", "Referans alan", "m²"), ("aspect_ratio", "Açıklık oranı", ""),
              ("mac_m", "Ortalama aerodinamik veter", "m"), ("lref_m", "Referans uzunluk", "m"),
              ("le_sweep_deg", "Hücum kenarı ok açısı", "°"), ("root_t_c", "Kök t/c", ""),
              ("tip_t_c", "Uç t/c", ""), ("length_x_m", "Boy (x)", "m"), ("span_y_m", "Genişlik (y)", "m"),
              ("height_z_m", "Yükseklik (z)", "m"), ("wetted_area_m2", "Islak alan", "m²"),
              ("frontal_area_m2", "Ön alan", "m²"), ("volume_m3", "Hacim", "m³"),
              ("symmetric", "Yarım model (simetri)", "")]


def _fmt(v, nd=4):
    if isinstance(v, bool):
        return "Evet" if v else "Hayır"
    if isinstance(v, (int, float)) and v is not None:
        return f"{v:.{nd}g}"
    return html.escape(str(v)) if v is not None else "–"


def write_report(st: Study) -> Path:
    d = st.data
    geo = d.get("geometry", {})
    flow = d.get("flow", {})
    rows_geo = "".join(f"<tr><td>{lab}</td><td>{_fmt(geo[k])} {u}</td></tr>"
                       for k, lab, u in GEO_LABELS if k in geo)
    flow_rows = "".join(f"<tr><td>{lab}</td><td>{_fmt(flow.get(k))} {u}</td></tr>" for k, lab, u in (
        ("velocity", "Hız", "m/s"), ("altitude_m", "İrtifa (ISA)", "m"), ("density", "Yoğunluk", "kg/m³"),
        ("kinematic_viscosity", "Kinematik viskozite", "m²/s"), ("mach", "Mach", ""),
        ("reynolds", "Reynolds (ref. uzunluk)", ""), ("dynamic_pressure_Pa", "Dinamik basınç", "Pa")) if k in flow)
    res = d.get("results") or []
    cols = [("alpha_deg", "α [°]"), ("CL", "CL"), ("CD", "CD"), ("CM", "Cm"), ("lift_N", "Kaldırma [N]"),
            ("drag_N", "Sürükleme [N]"), ("L_over_D", "L/D"), ("drag_pressure_N", "Basınç sürük. [N]"),
            ("drag_viscous_N", "Sürtünme sürük. [N]"), ("cells", "Hücre"), ("converged_hint", "Yakınsama")]
    cols = [c for c in cols if any(r.get(c[0]) is not None for r in res)]
    head = "".join(f"<th>{c[1]}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_fmt(r.get(k))}</td>" for k, _ in cols) + "</tr>" for r in res)
    comp = ""
    if d.get("comparison"):
        comp = "<h2>Hızlı model karşılaştırması</h2><table><tr><th>α [°]</th><th>CL</th><th>CD</th><th>L/D</th></tr>" + \
            "".join(f"<tr><td>{_fmt(r['alpha_deg'])}</td><td>{_fmt(r['CL'])}</td><td>{_fmt(r['CD'])}</td>"
                    f"<td>{_fmt(r['L_over_D'])}</td></tr>" for r in d["comparison"]) + "</table>"
    opt = ""
    if d.get("optimization"):
        o = d["optimization"]
        opt = "<h2>Optimizasyon</h2><table>" + "".join(
            f"<tr><td>{html.escape(k)}</td><td>{_fmt(v)}</td></tr>" for k, v in o["variables"].items()) + \
            f"<tr><td>Değerlendirme sayısı</td><td>{o['n_evals']}</td></tr>" + \
            f"<tr><td>Amaç</td><td>{html.escape(str(o.get('objective')))}</td></tr></table>"
    imgs = "".join(f'<figure><img src="data:image/png;base64,{_b64(st.path / f)}"/><figcaption>{cap}</figcaption></figure>'
                   for f, cap in (("geometry.png", "Geometri"), ("polar.png", "Sonuç grafikleri"))
                   if (st.path / f).exists())
    method = {
        "quick": "Vortex Lattice Method (kamber yüzeyi, Trefftz düzlemi indüklenmiş direnç), Polhamus hücum kenarı "
                 "emme analojisi (ok açısı ≥ 45°), türbülanslı düz levha + form faktörü ile sürtünme direnci.",
        "cfd": "OpenFOAM simpleFoam, sıkıştırılamaz sürekli RANS, k-ω SST türbülans modeli, snappyHexMesh ağı, "
               "serbest akış sınır koşulları, y = 0 simetri düzlemi (yarım model, kuvvetler tam kanat için).",
    }
    method["external"] = method["cfd"].replace("y = 0 simetri düzlemi (yarım model, kuvvetler tam kanat için)",
                                               "yarım/tam model seçime göre")
    method["optimization"] = "Hızlı çözücü veya CFD ile differential evolution / Nelder-Mead; kısıtlar ceza fonksiyonuyla."
    created = datetime.fromtimestamp(d["created"]).strftime("%d.%m.%Y %H:%M")
    doc = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>{html.escape(d['name'])} – Rapor</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1b1b1a;max-width:1000px;margin:32px auto;padding:0 20px;line-height:1.45}}
h1{{font-size:26px;margin:0}} h2{{font-size:18px;margin-top:28px;border-bottom:1px solid #e4e3df;padding-bottom:6px}}
.meta{{color:#6b6a66;margin:6px 0 20px}} table{{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}}
td,th{{border:1px solid #e4e3df;padding:6px 8px;text-align:left}} th{{background:#f4f3ef}}
td:not(:first-child){{font-variant-numeric:tabular-nums}} .grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
figure{{margin:16px 0}} img{{max-width:100%;border:1px solid #e4e3df;border-radius:8px}} figcaption{{color:#6b6a66;font-size:12px}}
.note{{background:#f7f6f2;border-left:3px solid #2a78d6;padding:10px 14px;font-size:13px}}
@media print{{body{{margin:0}} figure{{break-inside:avoid}}}}
</style></head><body>
<h1>{html.escape(d['name'])}</h1>
<div class="meta">{KIND_LABEL.get(d['kind'], d['kind'])} · {created} · {html.escape(d.get('runner') or '')}</div>
<div class="grid"><div><h2>Geometri</h2><table>{rows_geo}</table></div>
<div><h2>Akış koşulları</h2><table>{flow_rows}</table></div></div>
<h2>Sonuçlar</h2><table><tr>{head}</tr>{body}</table>
{comp}{opt}
{imgs}
<h2>Yöntem</h2><p class="note">{method.get(d['kind'], '')}<br>Kuvvetler: L = CL·q·S, D = CD·q·S; S = referans alan,
q = ½ρV². Kaldırma serbest akışa dik, sürükleme paraleldir. CFD sonuçları ağ bağımsızlığı çalışmasıyla doğrulanmalıdır.</p>
<p class="meta">Delta-Wing CFD aracı ile oluşturuldu.</p>
</body></html>"""
    p = st.path / "report.html"
    p.write_text(doc)
    return p
