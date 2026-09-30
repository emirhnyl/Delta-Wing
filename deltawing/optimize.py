"""Delta kanat planformu + airfoil optimizasyonu.

Tasarım değişkenleri konfigürasyonda noktalı yol ve [alt, üst] sınır olarak verilir:

    optimization:
      variables:
        wing.le_sweep_deg: [50, 70]
        wing.taper_ratio: [0.0, 0.3]
        airfoil.root.thickness: [0.04, 0.10]
        airfoil.root.camber: [0.0, 0.04]

Amaç fonksiyonları: max_LD, min_drag, max_lift, min_CD, max_CL
Mod:
  fixed_alpha : verilen hücum açısında değerlendir (varsayılan)
  fixed_lift  : her tasarım için hedef kaldırmayı veren hücum açısını bul (yalnız vlm),
                sonra amaç fonksiyonunu o açıda hesapla (örn. min_drag = seyir direnci)
Kısıtlar (ceza fonksiyonu ile): min_lift_N, max_drag_N, min_CL, min_LD,
  min_volume_m3, min_area_m2, max_area_m2, max_span_m, min_root_thickness
"""

from __future__ import annotations

import copy
import csv
import json
import math
import time
from pathlib import Path

import numpy as np
import yaml
from scipy import optimize as so

from .config import get_path, set_path
from .geometry import DeltaWing
from .vlm import QuickAero

OBJECTIVES = {
    "max_ld": lambda r: -r["L_over_D"],
    "min_drag": lambda r: r["drag_N"],
    "max_lift": lambda r: -r["lift_N"],
    "min_cd": lambda r: r["CD"],
    "max_cl": lambda r: -r["CL"],
}


class _Budget(Exception):
    pass


def apply_design(cfg: dict, names: list[str], x) -> dict:
    c = copy.deepcopy(cfg)
    for n, v in zip(names, x):
        set_path(c, n, float(v))
    return c


def _alpha_for_lift(qa: QuickAero, target_n: float) -> float:
    f = lambda a: qa.analyze(a).lift_N - target_n  # noqa: E731
    lo, hi = -5.0, 30.0
    if f(hi) < 0:
        raise ValueError("hedef kaldırmaya 30 derecede bile ulaşılamıyor")
    return so.brentq(f, lo, hi, xtol=1e-4)


def evaluate(cfg: dict, fidelity: str = "vlm", case_dir: Path | None = None) -> dict:
    """Tek tasarımın aerodinamik sonucunu ve geometri özetini döndürür."""
    o = cfg["optimization"]
    wing = DeltaWing.from_config(cfg)
    geo = wing.summary()
    if fidelity == "vlm":
        qa = QuickAero(wing, cfg)
        alpha = float(o["alpha_deg"])
        if str(o.get("mode", "fixed_alpha")).lower() == "fixed_lift":
            alpha = _alpha_for_lift(qa, float(o["lift_target_N"]))
        res = qa.analyze(alpha).to_dict()
    elif fidelity == "openfoam":
        from .openfoam import run_openfoam

        res = run_openfoam(wing, cfg, float(o["alpha_deg"]), case_dir)
    else:
        raise ValueError(f"Bilinmeyen fidelity: {fidelity}")
    res.update({f"geo_{k}": v for k, v in geo.items()})
    return res


def constraint_violation(res: dict, cons: dict) -> float:
    """Normalize edilmiş kısıt ihlallerinin kareleri toplamı (0 = uygun)."""
    checks = {
        "min_lift_N": (res["lift_N"], +1),
        "max_drag_N": (res["drag_N"], -1),
        "min_CL": (res["CL"], +1),
        "min_LD": (res["L_over_D"], +1),
        "min_volume_m3": (res["geo_volume_m3"], +1),
        "min_area_m2": (res["geo_area_m2"], +1),
        "max_area_m2": (res["geo_area_m2"], -1),
        "max_span_m": (res["geo_span_m"], -1),
        "min_root_thickness": (res["geo_root_t_c"], +1),
    }
    v = 0.0
    for key, limit in (cons or {}).items():
        if key not in checks:
            raise ValueError(f"Bilinmeyen kısıt: {key}")
        val, sign = checks[key]
        g = sign * (val - float(limit)) / max(abs(float(limit)), 1e-9)
        if g < 0:
            v += g * g
    return v


def run_optimization(cfg: dict, log=print) -> dict:
    o = cfg["optimization"]
    variables = o.get("variables") or {}
    if not variables:
        raise ValueError("optimization.variables boş - en az bir tasarım değişkeni tanımlayın")
    names = list(variables)
    bounds = np.array([variables[n] for n in names], dtype=float)
    fidelity = str(o.get("fidelity", "vlm")).lower()
    objective = OBJECTIVES[str(o.get("objective", "max_LD")).lower()]
    cons = o.get("constraints") or {}
    max_evals = int(o.get("max_evals", 300))
    out = Path(o.get("output_dir", "runs/optimization"))
    out.mkdir(parents=True, exist_ok=True)
    penalty_w = float(o.get("penalty_weight", 1e3))

    history: list[dict] = []
    best = {"f": math.inf}
    csv_path = out / "history.csv"
    csv_file = csv_path.open("w", newline="")
    writer = None
    t0 = time.time()

    def lo_hi(u):
        return bounds[:, 0] + np.clip(u, 0.0, 1.0) * (bounds[:, 1] - bounds[:, 0])

    def fun(u):
        nonlocal writer
        if len(history) >= max_evals:
            raise _Budget
        x = lo_hi(np.asarray(u))
        c = apply_design(cfg, names, x)
        i = len(history)
        rec = {"eval": i, **{n: float(v) for n, v in zip(names, x)}}
        try:
            res = evaluate(c, fidelity, out / f"eval_{i:04d}")
            obj = float(objective(res))
            viol = constraint_violation(res, cons)
            # ceza ölçeği amaç büyüklüğüne göre
            f = obj + penalty_w * viol * max(abs(obj), 1.0)
            rec.update({k: res[k] for k in ("alpha_deg", "CL", "CD", "lift_N", "drag_N", "L_over_D")})
            rec.update({"geo_area_m2": res["geo_area_m2"], "geo_volume_m3": res["geo_volume_m3"],
                        "violation": viol, "objective": obj, "f": f, "ok": True})
        except _Budget:
            raise
        except Exception as e:  # noqa: BLE001 - geçersiz geometri / çözücü hatası
            f = 1e6
            rec.update({"f": f, "ok": False, "error": str(e)[:200]})
        history.append(rec)
        if writer is None:
            keys = list(rec) + [k for k in ("alpha_deg", "CL", "CD", "lift_N", "drag_N", "L_over_D",
                                            "geo_area_m2", "geo_volume_m3", "violation",
                                            "objective", "error") if k not in rec]
            writer = csv.DictWriter(csv_file, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
        writer.writerow(rec)
        csv_file.flush()
        if f < best["f"]:
            best.update(rec)
            best["x"] = x.tolist()
            if rec.get("ok"):
                log(f"[{i:4d}] yeni en iyi f={f:.5g}  CL={rec['CL']:.4f} CD={rec['CD']:.5f} "
                    f"L/D={rec['L_over_D']:.3f}  " + "  ".join(f"{n}={v:.4g}" for n, v in zip(names, x)))
        return f

    method = str(o.get("method", "differential_evolution")).lower()
    nvar = len(names)
    try:
        if method == "differential_evolution":
            popsize = int(o.get("popsize", 12))
            maxiter = max(1, max_evals // (popsize * nvar) + 1)
            so.differential_evolution(fun, [(0.0, 1.0)] * nvar, maxiter=maxiter, popsize=popsize,
                                      seed=int(o.get("seed", 1)), polish=False, tol=1e-8,
                                      init="sobol" if nvar > 1 else "latinhypercube")
        elif method in ("nelder-mead", "powell"):
            x0 = o.get("x0")
            u0 = (np.array([(get_path(cfg, n) if x0 is None else x0[k]) for k, n in enumerate(names)],
                           dtype=float) - bounds[:, 0]) / (bounds[:, 1] - bounds[:, 0])
            so.minimize(fun, np.clip(u0, 0, 1), method="Nelder-Mead" if method == "nelder-mead" else "Powell",
                        bounds=[(0.0, 1.0)] * nvar if method == "powell" else None,
                        options={"maxfev": max_evals, "xatol": 1e-4, "fatol": 1e-6})
        else:
            raise ValueError(f"Bilinmeyen yöntem: {method}")
    except _Budget:
        log(f"Değerlendirme bütçesi ({max_evals}) doldu.")
    finally:
        csv_file.close()

    if "x" not in best:
        raise RuntimeError("Hiçbir geçerli tasarım bulunamadı")
    best_cfg = apply_design(cfg, names, best["x"])
    best_cfg.pop("_base_dir", None)
    (out / "best_config.yaml").write_text(yaml.safe_dump(best_cfg, sort_keys=False, allow_unicode=True))
    summary = {
        "variables": dict(zip(names, best["x"])),
        "result": {k: best.get(k) for k in ("alpha_deg", "CL", "CD", "lift_N", "drag_N", "L_over_D",
                                            "geo_area_m2", "geo_volume_m3", "violation")},
        "n_evals": len(history),
        "fidelity": fidelity,
        "objective": o.get("objective"),
        "elapsed_s": time.time() - t0,
    }
    (out / "best_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    summary["history"] = history
    return summary
