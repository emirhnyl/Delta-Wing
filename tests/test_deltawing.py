"""Temel doğrulama testleri:  python -m pytest tests -q"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deltawing import vlm  # noqa: E402
from deltawing.airfoil import cosine_spacing, cst, make_airfoil  # noqa: E402
from deltawing.config import get_path, load_config, set_path  # noqa: E402
from deltawing.geometry import DeltaWing, mesh_is_closed  # noqa: E402
from deltawing.openfoam import OpenFOAMCase  # noqa: E402


def flat(**wing):
    return load_config(overrides={"wing": wing, "airfoil": {"root": {"code": "0006"},
                                                             "tip": {"code": "0006"}}})


def test_naca_thickness():
    af = make_airfoil({"type": "naca4", "code": "0012"}, 120)
    assert af.max_thickness == pytest.approx(0.12, abs=2e-3)
    assert af.max_thickness_pos == pytest.approx(0.30, abs=0.02)
    cam = make_airfoil({"type": "naca4", "code": "4412"}, 120)
    assert cam.camber.max() == pytest.approx(0.04, abs=1e-3)


def test_cst_closed_te():
    af = cst(cosine_spacing(50), [0.17, 0.16, 0.15], [-0.17, -0.16, -0.15])
    assert af.y_upper[-1] == pytest.approx(af.y_lower[-1])
    assert af.max_thickness > 0.05


def test_planform_properties():
    w = DeltaWing.from_config(load_config())
    assert w.area == pytest.approx(0.5 * 1.2 * (1.0 + 0.05))
    assert w.aspect_ratio == pytest.approx(1.2**2 / w.area)


@pytest.mark.parametrize("ext", [0.0, 0.02])
def test_surface_watertight_and_volume(ext):
    w = DeltaWing.from_config(load_config())
    v, t = w.surface_mesh(20, 0.02, ext)
    assert mesh_is_closed(t)
    p = v[t]
    vol = np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6.0
    assert vol > 0  # dışa bakan normaller
    if ext == 0.0:
        assert 2 * vol == pytest.approx(w.volume(), rel=0.01)


def test_vlm_bertin_smith_example():
    """Bertin & Smith Örnek 7.2: 45° ok açılı, AR=5, 4 açıklık paneli -> CLα = 3.443/rad."""
    cfg = flat(span=1.0, root_chord=0.2, le_sweep_deg=45.0, taper_ratio=1.0)
    w = DeltaWing.from_config(cfg)
    lat = vlm.build_lattice(w, 1, 4)
    lat.y_edges[:] = np.linspace(0, w.semi_span, 5)  # kitaptaki eşit aralık
    y = lat.y_edges
    lat.a[:, 1], lat.b[:, 1] = y[:-1], y[1:]
    lat.a[:, 0] = 0.05 + y[:-1]
    lat.b[:, 0] = 0.05 + y[1:]
    ym = 0.5 * (y[:-1] + y[1:])
    lat.cp[:, 1], lat.cp[:, 0] = ym, 0.15 + ym
    r = vlm.solve_potential(w, lat, np.degrees(0.1))
    assert r.CL / 0.1 == pytest.approx(3.443, rel=0.01)


def test_delta_lift_slope_vs_datcom():
    for ar, datcom in ((1.0, 1.257), (2.0, 2.30)):
        b = ar / 2
        cfg = flat(span=b, root_chord=1.0, le_sweep_deg=np.degrees(np.arctan(1 / (b / 2))),
                   taper_ratio=0.0)
        qa = vlm.QuickAero(DeltaWing.from_config(cfg), cfg)
        assert qa.Kp == pytest.approx(datcom, rel=0.06)


def test_polhamus_vortex_lift_increases_cl():
    cfg = load_config()
    qa = vlm.QuickAero(DeltaWing.from_config(cfg), cfg)
    r = qa.analyze(12.0)
    assert r.CL_vortex > 0 and r.CL > r.CL_potential
    assert r.drag_N == pytest.approx(r.CD * r.q_Pa * r.area_m2)


def test_config_paths():
    cfg = load_config()
    set_path(cfg, "airfoil.root.thickness", 0.1)
    assert get_path(cfg, "airfoil.root.thickness") == 0.1


def test_openfoam_case_written(tmp_path):
    cfg = load_config(overrides={"openfoam": {"n_procs": 4}})
    case = OpenFOAMCase(DeltaWing.from_config(cfg), cfg, 8.0, tmp_path / "c").write()
    for f in ("system/blockMeshDict", "system/snappyHexMeshDict", "system/controlDict",
              "0.orig/U", "0.orig/p", "constant/triSurface/wing.stl", "Allrun"):
        assert (case / f).exists(), f
    assert "hierarchical" in (case / "system/decomposeParDict").read_text()


def test_optimizer_small(tmp_path):
    cfg = load_config(overrides={"optimization": {
        "max_evals": 40, "output_dir": str(tmp_path), "objective": "max_LD",
        "variables": {"wing.le_sweep_deg": [50, 70], "airfoil.root.thickness": [0.05, 0.1]}}})
    from deltawing.optimize import run_optimization
    res = run_optimization(cfg, log=lambda *_: None)
    assert res["n_evals"] <= 40
    assert (tmp_path / "best_config.yaml").exists()
