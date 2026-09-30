#!/usr/bin/env python3
"""Delta kanat 3B CFD analiz ve optimizasyon aracı - komut satırı arayüzü.

Örnekler
--------
  python run.py geometry  -c config/delta_wing.yaml
  python run.py quick     -c config/delta_wing.yaml
  python run.py cfd       -c config/delta_wing.yaml --alpha 4 8 12
  python run.py optimize  -c config/optimize_vlm.yaml --verify
  python run.py cfd -c config/delta_wing.yaml --set wing.le_sweep_deg=65 --set airfoil.root.thickness=0.06
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deltawing.config import load_config, set_path  # noqa: E402
from deltawing.geometry import DeltaWing, write_stl  # noqa: E402


def _overrides(cfg: dict, sets: list[str]) -> dict:
    for s in sets or []:
        key, _, val = s.partition("=")
        set_path(cfg, key.strip(), yaml.safe_load(val))
    return cfg


def _save_table(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _print_table(rows: list[dict], title: str) -> None:
    print(f"\n{title}")
    print(f"{'alpha[deg]':>10} {'CL':>9} {'CD':>9} {'Lift[N]':>10} {'Drag[N]':>10} {'L/D':>8}")
    for r in rows:
        print(f"{r['alpha_deg']:10.2f} {r['CL']:9.4f} {r['CD']:9.5f} {r['lift_N']:10.2f} "
              f"{r['drag_N']:10.3f} {r['L_over_D']:8.3f}")


def cmd_geometry(cfg, out: Path, args) -> None:
    from deltawing.plots import plot_geometry

    wing = DeltaWing.from_config(cfg)
    g = cfg["geometry"]
    v, t = wing.surface_mesh(int(g["n_span"]), float(g["min_tip_chord_ratio"]))
    write_stl(out / "wing_half.stl", v, t, "wing")
    fv, ft = wing.surface_mesh(int(g["n_span"]), float(g["min_tip_chord_ratio"]), full=True)
    write_stl(out / "wing_full.stl", fv, ft, "wing")
    plot_geometry(wing, out / "geometry.png")
    (out / "geometry.json").write_text(json.dumps(wing.summary(), indent=2))
    for k, val in wing.summary().items():
        print(f"  {k:>14}: {val:.5g}" if isinstance(val, (int, float)) else f"  {k:>14}: {val}")
    print(f"\nSTL ve görseller: {out}")


def _quick_polar(cfg, alphas):
    from deltawing.vlm import QuickAero

    qa = QuickAero(DeltaWing.from_config(cfg), cfg)
    return qa, [qa.analyze(a).to_dict() for a in alphas]


def cmd_quick(cfg, out: Path, args) -> None:
    from deltawing.plots import plot_polars

    alphas = args.alpha or cfg["flow"]["alpha_deg"]
    qa, rows = _quick_polar(cfg, alphas)
    print(f"Kp={qa.Kp:.4f}/rad  Kv={qa.Kv:.4f}  alpha0={qa.alpha0 * 57.2958:.3f} deg  "
          f"CD0={qa.visc['CD0']:.5f}  Re_mac={qa.visc['Re_mac']:.3g}  girdap kaldırması={'açık' if qa.vortex else 'kapalı'}")
    _print_table(rows, "Hızlı analiz (VLM + Polhamus + viskoz direnç)")
    _save_table(rows, out / "quick_polar.csv")
    (out / "quick_polar.json").write_text(json.dumps(rows, indent=2))
    plot_polars({"VLM + Polhamus": rows}, out / "quick_polar.png", "Hızlı analiz")
    print(f"\nSonuçlar: {out}")


def cmd_cfd(cfg, out: Path, args) -> None:
    from deltawing.openfoam import openfoam_available, run_openfoam

    wing = DeltaWing.from_config(cfg)
    alphas = args.alpha or cfg["flow"]["alpha_deg"]
    execute = not args.no_run
    if execute and not openfoam_available(cfg):
        print("UYARI: OpenFOAM bulunamadı - yalnızca vaka dosyaları yazılacak "
              "(openfoam.bashrc ayarını kontrol edin).")
        execute = False
    base = Path(cfg["openfoam"]["case_dir"]) if args.case_dir is None else Path(args.case_dir)
    rows = []
    for a in alphas:
        case = base / f"alpha_{a:+06.2f}".replace("+", "p").replace("-", "m")
        print(f"α = {a:g}°  ->  {case}")
        r = run_openfoam(wing, cfg, float(a), case, execute=execute)
        if r:
            rows.append(r)
            print(f"   CL={r['CL']:.4f}  CD={r['CD']:.5f}  L={r['lift_N']:.2f} N  D={r['drag_N']:.3f} N")
    if not rows:
        print(f"\nVakalar hazır. Her klasörde ./Allrun ile çalıştırın, sonra:\n"
              f"  python run.py collect --case-dir {base}")
        return
    _report_cfd(cfg, rows, out, base)


def _report_cfd(cfg, rows, out: Path, base: Path) -> None:
    from deltawing.plots import plot_polars

    rows = sorted(rows, key=lambda r: r["alpha_deg"])
    _print_table(rows, "3B RANS CFD sonuçları (OpenFOAM, tam kanat)")
    _save_table(rows, out / "cfd_results.csv")
    (out / "cfd_results.json").write_text(json.dumps(rows, indent=2))
    _, quick = _quick_polar(cfg, [r["alpha_deg"] for r in rows])
    plot_polars({"OpenFOAM RANS": rows, "VLM + Polhamus": quick}, out / "cfd_polar.png",
                "3B CFD ve hızlı model karşılaştırması")
    print(f"\nSonuçlar: {out}")


def cmd_collect(cfg, out: Path, args) -> None:
    from deltawing.openfoam import read_results

    base = Path(args.case_dir or cfg["openfoam"]["case_dir"])
    rows = []
    for case in sorted(base.glob("alpha_*")):
        try:
            rows.append(read_results(case, int(cfg["openfoam"]["average_last"])))
        except Exception as e:  # noqa: BLE001
            print(f"  {case.name}: okunamadı ({e})")
    if rows:
        _report_cfd(cfg, rows, out, base)


def cmd_optimize(cfg, out: Path, args) -> None:
    from deltawing.optimize import run_optimization
    from deltawing.plots import plot_geometry, plot_history

    if args.out:
        cfg["optimization"]["output_dir"] = str(out / "optimization")
    res = run_optimization(cfg)
    od = Path(cfg["optimization"]["output_dir"])
    plot_history(res["history"], od / "convergence.png", str(cfg["optimization"]["objective"]))
    best_cfg = load_config(od / "best_config.yaml")
    plot_geometry(DeltaWing.from_config(best_cfg), od / "best_geometry.png")
    print("\nEn iyi tasarım:")
    for k, v in res["variables"].items():
        print(f"  {k:>28} = {v:.5g}")
    r = res["result"]
    print(f"  α={r['alpha_deg']:.3f}°  CL={r['CL']:.4f}  CD={r['CD']:.5f}  "
          f"L={r['lift_N']:.2f} N  D={r['drag_N']:.3f} N  L/D={r['L_over_D']:.3f}")
    print(f"\nKayıtlar: {od}  (best_config.yaml, history.csv, convergence.png)")
    if args.verify:
        print("\nEn iyi tasarım 3B RANS CFD ile doğrulanıyor...")
        best_cfg["openfoam"]["case_dir"] = str(od / "verify")
        ns = argparse.Namespace(alpha=[r["alpha_deg"]], no_run=False, case_dir=None)
        cmd_cfd(best_cfg, od, ns)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["geometry", "quick", "cfd", "collect", "optimize"])
    p.add_argument("-c", "--config", default=str(Path(__file__).parent / "config" / "delta_wing.yaml"))
    p.add_argument("-o", "--out", default=None, help="çıktı klasörü (varsayılan runs/<config adı>)")
    p.add_argument("--alpha", type=float, nargs="+", help="hücum açıları [deg]")
    p.add_argument("--set", action="append", default=[], metavar="YOL=DEĞER",
                   help="konfigürasyonu geçersiz kıl, örn. --set wing.span=1.4")
    p.add_argument("--no-run", action="store_true", help="CFD vakalarını yaz ama çalıştırma")
    p.add_argument("--case-dir", default=None)
    p.add_argument("--verify", action="store_true", help="optimize: en iyi tasarımı CFD ile doğrula")
    args = p.parse_args(argv)

    cfg = _overrides(load_config(args.config), args.set)
    out = Path(args.out or Path("runs") / Path(args.config).stem)
    out.mkdir(parents=True, exist_ok=True)
    {"geometry": cmd_geometry, "quick": cmd_quick, "cfd": cmd_cfd, "collect": cmd_collect,
     "optimize": cmd_optimize}[args.command](cfg, out, args)


if __name__ == "__main__":
    main()
