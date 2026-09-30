"""Konfigürasyon yükleme ve noktalı-yol (``wing.le_sweep_deg``) erişimi."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "flow": {
        "velocity": 50.0,               # m/s
        "density": 1.225,               # kg/m^3
        "kinematic_viscosity": 1.5e-5,  # m^2/s
        "altitude_m": None,             # verilirse yoğunluk/viskozite/ses hızı ISA'dan hesaplanır
        "isa_dT": 0.0,
        "speed_of_sound": 340.3,        # m/s
        "alpha_deg": [0.0, 4.0, 8.0, 12.0],
    },
    "wing": {
        "type": "delta",       # delta | cropped_delta | double_delta | trapezoidal | rectangular | elliptical | custom
        "root_chord": 1.0,     # m
        "span": 1.2,           # m, uçtan uca (tam kanat)
        "le_sweep_deg": 60.0,  # hücum kenarı ok açısı
        "taper_ratio": 0.05,   # c_tip / c_root (saf delta ~0; ağ için min 0.02 uygulanır)
        "twist_tip_deg": 0.0,  # uçta burulma (negatif = washout)
        "twist_axis": 0.25,    # burulma ekseni (veter oranı)
        "dihedral_deg": 0.0,
    },
    "airfoil": {
        "n_points": 60,
        "root": {"type": "naca4", "code": "0008"},
        "tip": {"type": "naca4", "code": "0008"},
        "blend_exponent": 1.0,  # kök->uç karışım: w = eta**blend_exponent
    },
    "geometry": {
        "n_span": 40,               # STL açıklık istasyonu sayısı
        "min_tip_chord_ratio": 0.02,
    },
    "vlm": {
        "n_chord": 10,
        "n_span": 36,
        "vortex_lift": "auto",  # auto | on | off  (Polhamus hücum kenarı emme analojisi)
    },
    "openfoam": {
        "case_dir": "runs/baseline",
        "runner": "auto",        # auto | local | docker
        "docker_image": "opencfd/openfoam-default:2512",
        "bashrc": None,          # örn. /usr/lib/openfoam/openfoam2306/etc/bashrc (PATH'te değilse)
        "domain": {"upstream": 5.0, "downstream": 10.0, "lateral": 5.0, "vertical": 5.0},
        "base_cell_size": 0.25,  # kök veterine oranla blockMesh hücre boyu
        "surface_level": [4, 5],
        "feature_level": 5,
        "near_level": 3,         # kanat çevresi kutu
        "wake_level": 2,         # iz bölgesi kutusu
        "wake_length": 3.0,      # kök veteri cinsinden
        "layers": {"enabled": False, "n": 3, "expansion": 1.2, "final_thickness": 0.4},
        "turbulence_model": "kOmegaSST",
        "turbulence_intensity": 0.001,
        "viscosity_ratio": 10.0,
        "iterations": 1000,
        "write_interval": 500,
        "n_procs": 1,
        "average_last": 100,     # sonuç = son N iterasyonun ortalaması
        "function_objects": "auto",  # auto | true | false (false: kuvvetler alanlardan hesaplanır)
    },
    "optimization": {
        "fidelity": "vlm",              # vlm | openfoam
        "method": "differential_evolution",  # differential_evolution | nelder-mead | powell
        "alpha_deg": 8.0,
        "objective": "max_LD",          # max_LD | min_drag | max_lift | min_CD | max_CL
        "max_evals": 300,
        "seed": 1,
        "output_dir": "runs/optimization",
        "constraints": {},
        "variables": {},
    },
}


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            # airfoil tanımları tipe göre farklı alan setleri kullanır; tamamen değiştir
            if k in ("root", "tip"):
                out[k] = copy.deepcopy(v)
            else:
                out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if path is not None:
        path = Path(path)
        cfg = deep_merge(cfg, yaml.safe_load(path.read_text()) or {})
        cfg["_base_dir"] = str(path.parent.resolve())
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg


def _key(k: str):
    return int(k) if k.lstrip("-").isdigit() else k


def get_path(cfg: dict, dotted: str) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        node = node[_key(part)]
    return node


def set_path(cfg: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node: Any = cfg
    for part in parts[:-1]:
        node = node[_key(part)]
    node[_key(parts[-1])] = value
