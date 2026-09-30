"""Delta-Wing CFD Studio - yerel web sunucusu.

Çalıştırma:  python -m app.server   (veya macOS'ta start_mac.command)
Tarayıcı:    http://127.0.0.1:8765
"""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
import sys
import threading
import uuid
import webbrowser
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import yaml  # noqa: E402
from fastapi import Body as B, FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from deltawing import runner, setup_env, studies  # noqa: E402
from deltawing.airfoil import make_airfoil  # noqa: E402
from deltawing.atmosphere import isa  # noqa: E402
from deltawing.config import DEFAULTS, deep_merge  # noqa: E402
from deltawing.external import (AXES, UNITS, ExternalTransform, decimate_for_view,  # noqa: E402
                                load_mesh, mesh_metrics, transform)
from deltawing.geometry import Wing, write_stl  # noqa: E402
from deltawing.jobs import JobManager  # noqa: E402
from deltawing.planforms import WING_TYPES, type_defaults  # noqa: E402

DATA = ROOT / "data"
GEOMS = DATA / "geometries"
AIRFOILS = DATA / "airfoils"
DESIGNS = DATA / "designs"
SETTINGS_FILE = DATA / "settings.json"
for d in (GEOMS, AIRFOILS, DESIGNS):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Delta-Wing CFD Studio")
jobs = JobManager()
STATIC = Path(__file__).parent / "static"

AIRFOIL_PRESETS = [
    {"name": "NACA 0006 (ince simetrik)", "spec": {"type": "naca4", "code": "0006"}},
    {"name": "NACA 0008", "spec": {"type": "naca4", "code": "0008"}},
    {"name": "NACA 0010", "spec": {"type": "naca4", "code": "0010"}},
    {"name": "NACA 0012", "spec": {"type": "naca4", "code": "0012"}},
    {"name": "NACA 2408", "spec": {"type": "naca4", "code": "2408"}},
    {"name": "NACA 2412", "spec": {"type": "naca4", "code": "2412"}},
    {"name": "NACA 4412", "spec": {"type": "naca4", "code": "4412"}},
    {"name": "CST (örnek, kamberli)", "spec": {"type": "cst", "upper": [0.15, 0.17, 0.16, 0.14],
                                                  "lower": [-0.12, -0.08, -0.06, -0.04]}},
]


# --------------------------------------------------------------------------- ayarlar


def default_settings() -> dict:
    rec = setup_env.recommended_resources()
    return {"runner": "auto", "docker_image": runner.DEFAULT_IMAGE, "n_procs": rec["cpu"],
            "colima": rec, "theme": "auto"}


def load_settings() -> dict:
    s = default_settings()
    if SETTINGS_FILE.exists():
        try:
            s = deep_merge(s, json.loads(SETTINGS_FILE.read_text()))
        except json.JSONDecodeError:
            pass
    return s


def save_settings(s: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))


def full_config(cfg: dict | None) -> dict:
    """İstekten gelen konfigürasyonu varsayılanlar + uygulama ayarlarıyla birleştirir."""
    c = deep_merge(copy.deepcopy(DEFAULTS), cfg or {})
    s = load_settings()
    of = c["openfoam"]
    of["runner"] = s["runner"]
    of["docker_image"] = s["docker_image"]
    if not (cfg or {}).get("openfoam", {}).get("n_procs"):
        of["n_procs"] = int(s["n_procs"])
    c["_base_dir"] = str(ROOT)
    return c


def err(e: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(e))


def mesh_payload(v: np.ndarray, t: np.ndarray, max_tris: int = 60000) -> dict:
    v, t = decimate_for_view(v, t, max_tris)
    r = lambda a: np.round(a, 6).tolist()  # noqa: E731
    return {"x": r(v[:, 0]), "y": r(v[:, 1]), "z": r(v[:, 2]),
            "i": t[:, 0].tolist(), "j": t[:, 1].tolist(), "k": t[:, 2].tolist()}


# --------------------------------------------------------------------------- statik


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/vendor/plotly.min.js")
def plotly_js():
    import plotly
    return FileResponse(Path(plotly.__file__).parent / "package_data" / "plotly.min.js",
                        media_type="application/javascript")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# --------------------------------------------------------------------------- meta


@app.get("/api/meta")
def meta():
    return {
        "wing_types": WING_TYPES,
        "type_defaults": {k: type_defaults(k) for k in WING_TYPES},
        "defaults": {k: v for k, v in DEFAULTS.items() if k != "optimization"},
        "optimization_defaults": DEFAULTS["optimization"],
        "airfoil_presets": AIRFOIL_PRESETS + [
            {"name": f"Dosya: {p.name}", "spec": {"type": "file", "path": str(p.relative_to(ROOT))}}
            for p in sorted(AIRFOILS.glob("*.dat"))],
        "mesh_presets": studies.MESH_PRESETS,
        "units": list(UNITS), "axes": list(AXES),
        "settings": load_settings(),
        "version": "2.0",
    }


@app.get("/api/atmosphere")
def atmosphere(altitude: float = 0.0, dT: float = 0.0):
    return isa(altitude, dT)


# --------------------------------------------------------------------------- geometri


def _warnings(w: Wing, cfg: dict) -> list[str]:
    out = []
    if w.taper_ratio < float(cfg["geometry"]["min_tip_chord_ratio"]):
        out.append("Uç veteri çok küçük: CFD ağı için uçta en az "
                   f"%{100 * float(cfg['geometry']['min_tip_chord_ratio']):.0f} kök veteri kullanılır.")
    if min(w.root_airfoil.max_thickness, w.tip_airfoil.max_thickness) < 0.03:
        out.append("Çok ince profil (t/c < 0.03): CFD ağı ince bölgeyi çözemeyebilir; yüzey seviyesini artırın.")
    if 35 < w.le_sweep_deg < 50:
        out.append("Ok açısı 35-50° aralığında: girdap kaldırması modeli (Polhamus) sınırda, CFD ile doğrulayın.")
    if w.aspect_ratio > 12:
        out.append("Yüksek açıklık oranı: CFD alanı ve ağ sayısı büyük olacaktır.")
    return out


@app.post("/api/geometry/preview")
def geometry_preview(cfg: dict = B(...)):
    try:
        c = studies.prepare_config(full_config(cfg))
        w = Wing.from_config(c)
        v, t = w.surface_mesh(28, float(c["geometry"]["min_tip_chord_ratio"]), full=True)
        oy, ox = w.planform_outline()
        secs = []
        for eta, lab in ((0.0, "Kök"), (0.5, "%50"), (1.0, "Uç")):
            s = w.section(eta * w.semi_span)
            secs.append({"label": lab, "x": np.concatenate([s.x[::-1], s.x[1:]]).tolist(),
                         "z": np.concatenate([s.y_upper[::-1], s.y_lower[1:]]).tolist(),
                         "t_c": s.max_thickness})
        from deltawing.atmosphere import flow_numbers
        return {"summary": w.summary(), "mesh": mesh_payload(v, t),
                "outline": {"y": oy.tolist(), "x": ox.tolist()},
                "mac": {"y": w.mac_y, "x_le": w.mac_x_le, "c": w.mac},
                "sections": secs, "flow": {**c["flow"], **flow_numbers(c, w.mac)},
                "warnings": _warnings(w, c)}
    except (ValueError, KeyError) as e:
        err(e)


@app.post("/api/geometry/stl")
def geometry_stl(cfg: dict = B(...)):
    c = studies.prepare_config(full_config(cfg))
    w = Wing.from_config(c)
    v, t = w.surface_mesh(int(c["geometry"]["n_span"]), float(c["geometry"]["min_tip_chord_ratio"]), full=True)
    tmp = DATA / f"_export_{uuid.uuid4().hex[:6]}.stl"
    write_stl(tmp, v, t, "wing")
    data = tmp.read_bytes()
    tmp.unlink()
    return Response(data, media_type="model/stl",
                    headers={"Content-Disposition": f'attachment; filename="{w.kind}_wing.stl"'})


@app.post("/api/airfoil/preview")
def airfoil_preview(spec: dict = B(...)):
    try:
        a = make_airfoil(spec, 80, ROOT)
        return {"x": np.concatenate([a.x[::-1], a.x[1:]]).tolist(),
                "z": np.concatenate([a.y_upper[::-1], a.y_lower[1:]]).tolist(),
                "t_c": a.max_thickness, "x_t": a.max_thickness_pos, "camber": float(a.camber.max())}
    except Exception as e:  # noqa: BLE001
        err(e)


@app.post("/api/airfoil/upload")
async def airfoil_upload(file: UploadFile = File(...)):
    name = Path(file.filename or "profil.dat").name
    if not name.lower().endswith((".dat", ".txt")):
        name += ".dat"
    path = AIRFOILS / name
    path.write_bytes(await file.read())
    try:
        make_airfoil({"type": "file", "path": str(path)}, 60)
    except Exception as e:  # noqa: BLE001
        path.unlink(missing_ok=True)
        err(ValueError(f"Profil okunamadı: {e}"))
    return {"name": f"Dosya: {name}", "spec": {"type": "file", "path": str(path.relative_to(ROOT))}}


# --------------------------------------------------------------------------- tasarım kütüphanesi


@app.get("/api/designs")
def designs_list():
    out = []
    for p in sorted(DESIGNS.glob("*.yaml")):
        try:
            d = yaml.safe_load(p.read_text())
            out.append({"name": p.stem, "type": d.get("wing", {}).get("type", "delta"), "config": d,
                        "modified": p.stat().st_mtime})
        except yaml.YAMLError:
            continue
    return sorted(out, key=lambda d: -d["modified"])


@app.post("/api/designs/{name}")
def designs_save(name: str, cfg: dict = B(...)):
    safe = studies._slug(name)
    (DESIGNS / f"{safe}.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    return {"name": safe}


@app.delete("/api/designs/{name}")
def designs_delete(name: str):
    (DESIGNS / f"{studies._slug(name)}.yaml").unlink(missing_ok=True)
    return {"ok": True}


# --------------------------------------------------------------------------- hızlı analiz


@app.post("/api/quick")
def quick(req: dict = B(...)):
    try:
        cfg = full_config(req.get("config"))
        alphas = [float(a) for a in req.get("alphas") or cfg["flow"]["alpha_deg"]]
        out = studies.quick_analysis(cfg, alphas, req.get("span_alpha"))
        if req.get("save"):
            out.update(studies.save_quick_study(cfg, req.get("name") or "Hızlı analiz", alphas))
        return out
    except (ValueError, KeyError) as e:
        err(e)


# --------------------------------------------------------------------------- CFD


def _apply_mesh(cfg: dict, mesh: dict | None) -> dict:
    mesh = mesh or {}
    preset = studies.MESH_PRESETS.get(mesh.get("preset", "orta"), {})
    of = cfg["openfoam"]
    for k in ("base_cell_size", "surface_level", "feature_level", "near_level", "wake_level", "iterations"):
        if k in preset:
            of[k] = copy.deepcopy(preset[k])
    for k in ("base_cell_size", "surface_level", "feature_level", "near_level", "wake_level",
              "iterations", "n_procs", "turbulence_model", "average_last"):
        if mesh.get(k) not in (None, ""):
            of[k] = mesh[k]
    if "layers" in mesh:
        of["layers"] = {**of["layers"], **mesh["layers"]}
    if "domain" in mesh:
        of["domain"] = {**of["domain"], **mesh["domain"]}
    of["write_interval"] = max(100, int(of["iterations"]))
    return cfg


@app.post("/api/cfd/start")
def cfd_start(req: dict = B(...)):
    cfg = _apply_mesh(full_config(req.get("config")), req.get("mesh"))
    alphas = [float(a) for a in req.get("alphas") or [4, 8, 12]]
    name = req.get("name") or "CFD analizi"
    source = None
    if req.get("external"):
        ex = dict(req["external"])
        gdir = GEOMS / ex["id"]
        ex["path"] = str(next(gdir.glob("model.*")))
        source = {"external": ex}
    if runner.resolve_runner(cfg) == "none":
        err(RuntimeError("OpenFOAM bulunamadı. Önce 'Kurulum' sayfasından Docker + OpenFOAM kurun."))
    job = jobs.submit("cfd", f"{name} (α = {', '.join(f'{a:g}' for a in alphas)}°)",
                      lambda j: studies.cfd_job(j, cfg, alphas, name, source),
                      meta={"alphas": alphas, "iterations": cfg["openfoam"]["iterations"],
                            "external": bool(source)})
    return {"job_id": job.id}


# --------------------------------------------------------------------------- harici geometri


@app.post("/api/external/upload")
async def external_upload(file: UploadFile = File(...)):
    name = Path(file.filename or "model.stl").name
    ext = Path(name).suffix.lower()
    if ext not in (".stl", ".obj"):
        err(ValueError("Yalnızca STL veya OBJ dosyaları desteklenir"))
    gid = uuid.uuid4().hex[:10]
    gdir = GEOMS / gid
    gdir.mkdir(parents=True)
    path = gdir / f"model{ext}"
    path.write_bytes(await file.read())
    try:
        v, t = load_mesh(path)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(gdir)
        err(ValueError(f"Geometri okunamadı: {e}"))
    lo, hi = v.min(axis=0), v.max(axis=0)
    ext_ = float((hi - lo).max())
    guess = "mm" if ext_ > 50 else ("cm" if ext_ > 8 else "m")
    (gdir / "meta.json").write_text(json.dumps({"name": name, "id": gid}))
    return {"id": gid, "name": name, "n_triangles": int(len(t)), "raw_bbox": [lo.tolist(), hi.tolist()],
            "unit_guess": guess}


@app.get("/api/external/list")
def external_list():
    out = []
    for d in sorted(GEOMS.iterdir(), key=lambda p: -p.stat().st_mtime):
        m = d / "meta.json"
        if m.exists():
            out.append(json.loads(m.read_text()))
    return out


@app.post("/api/external/prepare")
def external_prepare(req: dict = B(...)):
    try:
        gdir = GEOMS / req["id"]
        path = next(gdir.glob("model.*"))
        t = ExternalTransform.from_dict(req.get("transform"))
        v0, tris = load_mesh(path)
        v = transform(v0, t)
        m = mesh_metrics(v, tris)
        warn = []
        if not m["closed"]:
            warn.append(f"Yüzey kapalı değil ({m['open_edges']} açık, {m['nonmanifold_edges']} manifold olmayan kenar). "
                        "snappyHexMesh çoğu durumda yine çalışır; sorun olursa CAD'de onarın.")
        if t.symmetric and abs(0.5 * (m["bbox_min"][1] + m["bbox_max"][1])) > 0.05 * m["span_y_m"]:
            warn.append("Yarım model için gövde y = 0'a göre simetrik ve ortalanmış olmalı ('Merkezle' açık olmalı).")
        if m["length_x_m"] > 100 or m["length_x_m"] < 0.005:
            warn.append("Boyutlar olağan dışı; birim seçimini kontrol edin.")
        lo = np.array(m["bbox_min"])
        return {"metrics": m, "mesh": mesh_payload(v, tris, 80000), "warnings": warn,
                "suggest": {"aref": m["planform_area_m2"], "lref": m["length_x_m"],
                            "cofr": [float(lo[0] + 0.25 * m["length_x_m"]), 0.0, 0.0]}}
    except (StopIteration, FileNotFoundError):
        err(FileNotFoundError("Geometri bulunamadı"), 404)
    except ValueError as e:
        err(e)


# --------------------------------------------------------------------------- optimizasyon


@app.post("/api/optimize/start")
def optimize_start(req: dict = B(...)):
    cfg = full_config(req.get("config"))
    cfg["optimization"] = deep_merge(DEFAULTS["optimization"], req.get("optimization") or {})
    if req.get("mesh"):
        _apply_mesh(cfg, req["mesh"])
    if not cfg["optimization"].get("variables"):
        err(ValueError("En az bir tasarım değişkeni seçin"))
    name = req.get("name") or "Optimizasyon"
    exclusive = cfg["optimization"].get("fidelity") == "openfoam"
    job = jobs.submit("optimization", name, lambda j: studies.optimization_job(j, cfg, name),
                      exclusive=exclusive, meta={"max_evals": cfg["optimization"]["max_evals"]})
    return {"job_id": job.id}


# --------------------------------------------------------------------------- işler


@app.get("/api/jobs")
def jobs_list():
    return jobs.list()


@app.get("/api/jobs/{job_id}")
def job_get(job_id: str, since: int = 0):
    j = jobs.jobs.get(job_id)
    if not j:
        err(KeyError("İş bulunamadı"), 404)
    return JSONResponse(studies._jsonable(j.to_dict(log_since=since)))


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    return {"ok": jobs.cancel(job_id)}


# --------------------------------------------------------------------------- çalışmalar


@app.get("/api/studies")
def studies_list():
    return studies.list_studies()


@app.get("/api/studies/{sid}")
def study_get(sid: str):
    try:
        return studies.Study.load(sid).data
    except FileNotFoundError:
        err(FileNotFoundError("Çalışma bulunamadı"), 404)


@app.delete("/api/studies/{sid}")
def study_delete(sid: str):
    studies.delete_study(sid)
    return {"ok": True}


@app.get("/api/studies/{sid}/report", response_class=HTMLResponse)
def study_report(sid: str):
    st = studies.Study.load(sid)
    p = st.path / "report.html"
    if not p.exists():
        studies.write_report(st)
    return p.read_text()


@app.get("/api/studies/{sid}/file/{name}")
def study_file(sid: str, name: str):
    p = (studies.STUDIES / sid / name).resolve()
    if studies.STUDIES.resolve() not in p.parents or not p.exists():
        err(FileNotFoundError(name), 404)
    return FileResponse(p)


@app.get("/api/studies/{sid}/viz")
def study_viz(sid: str):
    st = studies.Study.load(sid)
    return {"study_id": sid, "cases": studies.viz_cases(st)}


@app.get("/api/studies/{sid}/viz/{case}/{name}")
def study_viz_file(sid: str, case: str, name: str):
    base = (studies.STUDIES / sid / "cases" / case / "viz").resolve()
    p = (base / name).resolve()
    if base not in p.parents or not p.exists():
        err(FileNotFoundError(name), 404)
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


@app.post("/api/studies/{sid}/viz/{case}/build")
def study_viz_build(sid: str, case: str):
    j = jobs.submit("viz", f"Akış görselleştirmesi ({case})", lambda job: studies.viz_job(job, sid, case),
                    exclusive=False, meta={"study_id": sid, "case": case})
    return {"job_id": j.id}


@app.get("/api/studies/{sid}/download")
def study_download(sid: str, cases: bool = False):
    st = studies.Study.load(sid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in st.path.rglob("*"):
            if p.is_dir():
                continue
            rel = p.relative_to(st.path)
            if not cases and rel.parts and rel.parts[0] == "cases":
                keep = {"case_info.json", "log.simpleFoam", "log.checkMesh", "log.snappyHexMesh"}
                is_img = "viz" in rel.parts and p.suffix == ".png"
                if p.name not in keep and "postProcessing" not in rel.parts and not is_img:
                    continue
            if "processor" in "/".join(rel.parts):
                continue
            z.write(p, str(rel))
        import csv
        s = io.StringIO()
        rows = st.data.get("results") or []
        if rows:
            keys = list(dict.fromkeys(k for r in rows for k in r))
            w = csv.DictWriter(s, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
            z.writestr("results.csv", s.getvalue())
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{sid}.zip"'})


@app.post("/api/studies/{sid}/open_folder")
def study_open_folder(sid: str):
    p = studies.STUDIES / sid
    if sys.platform == "darwin":
        os.system(f'open "{p}"')
    return {"path": str(p)}


# --------------------------------------------------------------------------- kurulum


@app.get("/api/setup/status")
def setup_status():
    return setup_env.status(full_config({}))


@app.post("/api/setup/action")
def setup_action(req: dict = B(...)):
    action = req.get("action")
    s = load_settings()
    cfg = full_config({})
    if action == "install_homebrew":
        j = jobs.submit("setup", "Homebrew kurulumu (Terminal)", setup_env.install_homebrew, exclusive=False)
    elif action == "install_colima":
        j = jobs.submit("setup", "Colima + Docker CLI kurulumu", setup_env.install_colima)
    elif action == "start_colima":
        c = {**s["colima"], **(req.get("resources") or {})}
        s["colima"] = c
        s["n_procs"] = int(c["cpu"])
        save_settings(s)
        j = jobs.submit("setup", f"Docker VM başlat ({c['cpu']} CPU, {c['memory']} GB)",
                        lambda job: setup_env.start_colima(job, int(c["cpu"]), int(c["memory"]), int(c.get("disk", 40))))
    elif action == "stop_colima":
        j = jobs.submit("setup", "Docker VM durdur", setup_env.stop_colima)
    elif action == "open_docker_desktop":
        j = jobs.submit("setup", "Docker Desktop aç", setup_env.open_docker_desktop, exclusive=False)
    elif action == "pull_image":
        img = req.get("image") or s["docker_image"]
        j = jobs.submit("setup", f"OpenFOAM imajı indir ({img})", lambda job: setup_env.pull_image(job, img))
    elif action == "test_openfoam":
        j = jobs.submit("setup", "OpenFOAM test çalıştırması",
                        lambda job: setup_env.test_openfoam(job, cfg, DATA))
    elif action == "auto":
        j = jobs.submit("setup", "Otomatik kurulum", lambda job: auto_setup(job))
    else:
        err(ValueError(f"Bilinmeyen eylem: {action}"))
    return {"job_id": j.id}


def auto_setup(job):
    """Eksik adımları sırayla tamamlar."""
    s = load_settings()
    cfg = full_config({})
    st = setup_env.status(cfg)
    if st["local_openfoam"]["ok"] and s["runner"] in ("auto", "local"):
        job.log("Yerel OpenFOAM bulundu; Docker gerekmiyor.")
    else:
        if not st["docker_engine"]["ok"]:
            if not st["is_mac"]:
                raise RuntimeError("Docker motoru çalışmıyor (Linux: 'sudo systemctl start docker').")
            has_engine = st["colima"]["ok"] or st["docker_desktop"]["ok"]
            if not has_engine or not st["docker_cli"]["ok"]:
                if not st["homebrew"]["ok"]:
                    setup_env.install_homebrew(job)
                    raise RuntimeError("Homebrew gerekli: açılan Terminal penceresinde kurulumu tamamlayın, "
                                       "ardından 'Otomatik kurulum'u tekrar başlatın.")
                job.set(0.1, "Colima + Docker CLI kuruluyor")
                setup_env.install_colima(job)
            if runner.which("colima"):
                job.set(0.35, "Docker VM başlatılıyor")
                c = s["colima"]
                setup_env.start_colima(job, int(c["cpu"]), int(c["memory"]), int(c.get("disk", 40)))
            elif st["docker_desktop"]["ok"]:
                setup_env.open_docker_desktop(job)
                raise RuntimeError("Docker Desktop açıldı; motor hazır olunca tekrar deneyin.")
            if not runner.docker_available(20):
                raise RuntimeError("Docker motoru çalışmıyor.")
        img = s["docker_image"]
        if not runner.docker_image_present(img):
            job.set(0.5, "OpenFOAM imajı indiriliyor")
            setup_env.pull_image(job, img)
    job.set(0.85, "Test CFD çalıştırılıyor")
    res = setup_env.test_openfoam(job, full_config({}), DATA)
    return {"ok": True, "test": res}


@app.get("/api/settings")
def settings_get():
    return load_settings()


@app.post("/api/settings")
def settings_set(s: dict = B(...)):
    cur = deep_merge(load_settings(), s)
    save_settings(cur)
    return cur


def main():
    import argparse

    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()
    if not a.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{a.host}:{a.port}")).start()
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
