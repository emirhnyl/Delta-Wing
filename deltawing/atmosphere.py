"""Uluslararası Standart Atmosfer (ISA), 0-32 km."""

from __future__ import annotations

import math

R = 287.05287
G0 = 9.80665
GAMMA = 1.4
_LAYERS = [  # taban irtifa [m], taban sıcaklık [K], sıcaklık gradyanı [K/m]
    (0.0, 288.15, -0.0065),
    (11000.0, 216.65, 0.0),
    (20000.0, 216.65, 0.001),
    (32000.0, 228.65, 0.0028),
]


def isa(altitude_m: float, dT: float = 0.0) -> dict:
    """İrtifadaki sıcaklık, basınç, yoğunluk, viskozite ve ses hızı.

    dT: ISA sapması [K] (sıcak/soğuk gün).
    """
    h = min(max(float(altitude_m), -500.0), 32000.0)
    p = 101325.0
    T = 288.15
    for i, (hb, Tb, L) in enumerate(_LAYERS):
        top = _LAYERS[i + 1][0] if i + 1 < len(_LAYERS) else math.inf
        dh = min(h, top) - hb
        T = Tb + L * dh
        p = p * math.exp(-G0 * dh / (R * Tb)) if L == 0.0 else p * (T / Tb) ** (-G0 / (L * R))
        if h <= top:
            break
    T_act = T + dT
    rho = p / (R * T_act)
    mu = 1.458e-6 * T_act**1.5 / (T_act + 110.4)  # Sutherland
    return {
        "altitude_m": h,
        "temperature_K": T_act,
        "pressure_Pa": p,
        "density": rho,
        "dynamic_viscosity": mu,
        "kinematic_viscosity": mu / rho,
        "speed_of_sound": math.sqrt(GAMMA * R * T_act),
    }


def apply_atmosphere(cfg: dict) -> dict:
    """flow.altitude_m tanımlıysa yoğunluk, viskozite ve ses hızını ISA'dan doldurur."""
    f = cfg["flow"]
    if f.get("altitude_m") is not None:
        a = isa(float(f["altitude_m"]), float(f.get("isa_dT", 0.0)))
        f["density"] = a["density"]
        f["kinematic_viscosity"] = a["kinematic_viscosity"]
        f["speed_of_sound"] = a["speed_of_sound"]
    return cfg


def flow_numbers(cfg: dict, ref_length: float) -> dict:
    f = cfg["flow"]
    V = float(f["velocity"])
    return {
        "mach": V / float(f["speed_of_sound"]),
        "reynolds": V * ref_length / float(f["kinematic_viscosity"]),
        "dynamic_pressure_Pa": 0.5 * float(f["density"]) * V * V,
    }
