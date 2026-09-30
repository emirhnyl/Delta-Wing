"""Kanat tipleri, atmosfer, harici geometri ve web API testleri."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deltawing.atmosphere import isa  # noqa: E402
from deltawing.config import load_config  # noqa: E402
from deltawing.external import ExternalTransform, body_from_external, mesh_metrics  # noqa: E402
from deltawing.geometry import Wing, mesh_is_closed, write_stl  # noqa: E402
from deltawing.openfoam import OpenFOAMCase  # noqa: E402
from deltawing.planforms import WING_TYPES, type_defaults  # noqa: E402
from deltawing.vlm import QuickAero, span_loading  # noqa: E402


@pytest.mark.parametrize("kind", list(WING_TYPES))
def test_every_wing_type_builds_closed_surface(kind):
    cfg = load_config(overrides={"wing": type_defaults(kind)})
    w = Wing.from_config(cfg)
    for full in (False, True):
        _, t = w.surface_mesh(24, 0.02, 0.0 if full else 0.02, full=full)
        assert mesh_is_closed(t)
    r = QuickAero(w, cfg).analyze(5.0)
    assert r.CL > 0 and r.CD > 0


def test_rectangular_and_trapezoidal_area():
    w = Wing.from_config(load_config(overrides={"wing": {"type": "rectangular", "root_chord": 0.5, "span": 3.0}}))
    assert w.area == pytest.approx(1.5, rel=1e-6)
    assert w.mac == pytest.approx(0.5, rel=1e-6)
    t = Wing.from_config(load_config(overrides={"wing": {"type": "trapezoidal", "root_chord": 1.0, "span": 4.0,
                                                          "taper_ratio": 0.5, "sweep_deg": 0.0}}))
    assert t.area == pytest.approx(3.0, rel=1e-6)
    assert t.mac == pytest.approx(2 / 3 * (1 + 0.5 + 0.25) / 1.5, rel=1e-4)


def test_elliptical_span_efficiency_near_one():
    cfg = load_config(overrides={"wing": type_defaults("elliptical"), "vlm": {"n_span": 60, "n_chord": 6},
                                 "airfoil": {"root": {"code": "0006"}, "tip": {"code": "0006"}}})
    e = span_loading(QuickAero(Wing.from_config(cfg), cfg), 5.0)["e_span_efficiency"]
    assert e == pytest.approx(1.0, abs=0.02)


def test_double_delta_kink_validation():
    with pytest.raises(ValueError):
        Wing.from_config(load_config(overrides={"wing": {**type_defaults("double_delta"), "inner_sweep_deg": 85,
                                                          "kink_eta": 0.9}}))


def test_isa_standard_values():
    assert isa(0)["density"] == pytest.approx(1.225, rel=1e-3)
    assert isa(11000)["temperature_K"] == pytest.approx(216.65, abs=0.01)
    assert isa(5000)["density"] == pytest.approx(0.7364, rel=2e-3)


def test_external_import_orientation_and_areas(tmp_path):
    cfg = load_config()
    w = Wing.from_config(cfg)
    v, t = w.surface_mesh(30, 0.02, full=True)
    model = np.column_stack([v[:, 1], -v[:, 0], v[:, 2]]) * 1000.0  # akış -y, mm
    path = write_stl(tmp_path / "m.stl", model, t, "m")
    body, m = body_from_external(path, ExternalTransform(unit="mm", forward="-y", up="+z"))
    assert m["closed"]
    assert m["planform_area_m2"] == pytest.approx(w.area, rel=0.01)
    assert m["length_x_m"] == pytest.approx(v[:, 0].max() - v[:, 0].min(), rel=1e-6)
    assert m["volume_m3"] == pytest.approx(w.volume(), rel=0.01)
    assert body.verts[:, 0].min() == pytest.approx(0.0, abs=1e-9)
    case = OpenFOAMCase(body, cfg, 4.0, tmp_path / "case").write()
    bm = (case / "system/blockMeshDict").read_text()
    assert "symmetryPlane" not in bm  # tam model


def test_mesh_metrics_cube():
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)
    f = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    m = mesh_metrics(v, f)
    assert m["closed"] and m["volume_m3"] == pytest.approx(1.0)
    assert m["planform_area_m2"] == pytest.approx(1.0, rel=0.01)
    assert m["wetted_area_m2"] == pytest.approx(6.0)


def test_web_api_smoke():
    from fastapi.testclient import TestClient

    from app.server import app
    c = TestClient(app)
    meta = c.get("/api/meta").json()
    assert set(meta["wing_types"]) == set(WING_TYPES)
    r = c.post("/api/geometry/preview", json={"wing": type_defaults("cropped_delta")})
    assert r.status_code == 200 and len(r.json()["mesh"]["i"]) > 100
    r = c.post("/api/quick", json={"config": {"flow": {"altitude_m": 2000}}, "alphas": [0, 5, 10]})
    assert r.status_code == 200 and len(r.json()["results"]) == 3
    assert r.json()["flow"]["density"] == pytest.approx(isa(2000)["density"])
    assert c.post("/api/geometry/preview", json={"wing": {"type": "yok"}}).status_code == 400
    assert c.get("/vendor/plotly.min.js").status_code == 200


def test_cfd_and_flow_visualization_end_to_end(tmp_path):
    """OpenFOAM (yerel veya Docker) varsa çok kaba bir CFD + görselleştirme; yoksa atlanır."""
    from deltawing.openfoam import openfoam_available, run_openfoam
    from deltawing.postprocess import build_visualization

    cfg = load_config(overrides={"openfoam": {
        "base_cell_size": 0.6, "surface_level": [3, 4], "feature_level": 4, "near_level": 2, "wake_level": 1,
        "iterations": 40, "write_interval": 40, "average_last": 5, "n_procs": 1}})
    if not openfoam_available(cfg):
        pytest.skip("OpenFOAM yok")
    res = run_openfoam(Wing.from_config(cfg), cfg, 8.0, tmp_path / "case")
    assert res["CL"] > 0
    meta = build_visualization(tmp_path / "case", log=lambda *_: None)
    assert "surface_cp.png" in meta["images"] and len(meta["slices"]) == 6
    for f in ("surface.json", "slices.json", "streamlines.json"):
        assert (tmp_path / "case" / "viz" / f).stat().st_size > 1000
