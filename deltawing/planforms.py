"""Kanat planform tipleri.

Her tip, yarı kanat için açıklık istasyonlarını (y, hücum kenarı x'i, veter,
ek burulma) üretir. Geometri, VLM ve CFD bu istasyonları parçalı doğrusal olarak
enterpole eder; böylece yeni bir planform tipi eklemek için yalnızca burada bir
fonksiyon yazmak yeterlidir.

``WING_TYPES`` aynı zamanda arayüzün parametre formlarını oluşturduğu şemadır.
"""

from __future__ import annotations

import math

import numpy as np


def _p(key, label, unit, default, lo, hi, step, help_=""):
    return {"key": key, "label": label, "unit": unit, "default": default,
            "min": lo, "max": hi, "step": step, "help": help_}


P_ROOT = _p("root_chord", "Kök veteri", "m", 1.0, 0.05, 50, 0.01)
P_SPAN = _p("span", "Açıklık (uçtan uca)", "m", 1.2, 0.05, 100, 0.01)
P_TAPER = _p("taper_ratio", "Sivrilme oranı (uç/kök)", "-", 0.05, 0.0, 1.0, 0.01,
             "Saf delta için 0; ağ üretiminde en az min_tip_chord_ratio kullanılır")
COMMON = [
    _p("twist_tip_deg", "Uç burulması", "°", 0.0, -10, 10, 0.1, "Negatif = washout (uç burnu aşağı)"),
    _p("dihedral_deg", "Dihedral", "°", 0.0, -15, 15, 0.5),
]

WING_TYPES: dict[str, dict] = {
    "delta": {
        "label": "Delta",
        "description": "Düz hücum kenarlı klasik delta kanat. Yüksek ok açısında girdap kaldırması üretir.",
        "params": [P_ROOT, P_SPAN, _p("le_sweep_deg", "Hücum kenarı ok açısı", "°", 60.0, 0, 84, 0.5), P_TAPER],
    },
    "cropped_delta": {
        "label": "Kırpılmış delta",
        "description": "Ucu kesilmiş delta; uç veteri belirgin (sivrilme 0.15-0.4).",
        "params": [P_ROOT, P_SPAN, _p("le_sweep_deg", "Hücum kenarı ok açısı", "°", 55.0, 0, 84, 0.5),
                   {**P_TAPER, "default": 0.25}],
    },
    "double_delta": {
        "label": "Çift delta (kırık hücum kenarı)",
        "description": "İç bölümde yüksek, dış bölümde düşük ok açılı (strake + ana kanat) planform.",
        "params": [P_ROOT, P_SPAN,
                   _p("kink_eta", "Kırılma konumu (yarı açıklık oranı)", "-", 0.35, 0.05, 0.95, 0.01),
                   _p("inner_sweep_deg", "İç ok açısı", "°", 75.0, 0, 85, 0.5),
                   _p("outer_sweep_deg", "Dış ok açısı", "°", 50.0, 0, 84, 0.5),
                   {**P_TAPER, "default": 0.08}],
    },
    "trapezoidal": {
        "label": "Ok açılı / sivrilen (trapez)",
        "description": "Klasik ok açılı ve sivrilen kanat (uçak kanadı, uçan kanat).",
        "params": [P_ROOT, _p("span", "Açıklık (uçtan uca)", "m", 3.0, 0.05, 100, 0.01),
                   {**P_TAPER, "default": 0.4},
                   _p("sweep_deg", "Ok açısı", "°", 25.0, -45, 75, 0.5),
                   _p("sweep_ref", "Ok açısı referans çizgisi (veter oranı)", "-", 0.25, 0, 1, 0.05,
                      "0 = hücum kenarı, 0.25 = çeyrek veter")],
    },
    "rectangular": {
        "label": "Dikdörtgen",
        "description": "Sabit veterli, ok açısız kanat (referans / doğrulama için).",
        "params": [_p("root_chord", "Veter", "m", 0.5, 0.05, 50, 0.01),
                   _p("span", "Açıklık (uçtan uca)", "m", 3.0, 0.05, 100, 0.01)],
    },
    "elliptical": {
        "label": "Eliptik",
        "description": "Eliptik veter dağılımı; düz çeyrek veter çizgisi (Spitfire tipi).",
        "params": [P_ROOT, _p("span", "Açıklık (uçtan uca)", "m", 3.0, 0.05, 100, 0.01),
                   _p("sweep_deg", "Çeyrek veter ok açısı", "°", 0.0, -30, 60, 0.5)],
    },
    "custom": {
        "label": "Özel (kesit tablosu)",
        "description": "Yarı kanat kesitlerini tablo olarak girin: y, hücum kenarı x, veter, burulma.",
        "params": [],
    },
}
for _t in WING_TYPES.values():
    _t["params"] = _t["params"] + COMMON

DEFAULT_CUSTOM_SECTIONS = [[0.0, 0.0, 1.0, 0.0], [0.25, 0.45, 0.55, 0.0], [0.6, 0.75, 0.25, -1.0]]


def type_defaults(kind: str) -> dict:
    d = {"type": kind}
    for p in WING_TYPES[kind]["params"]:
        d[p["key"]] = p["default"]
    if kind == "custom":
        d["sections"] = [list(s) for s in DEFAULT_CUSTOM_SECTIONS]
    return d


def _get(w: dict, key: str):
    if key in w:
        return float(w[key])
    kind = w.get("type", "delta")
    for p in WING_TYPES[kind]["params"]:
        if p["key"] == key:
            return float(p["default"])
    raise KeyError(key)


def stations(w: dict):
    """Yarı kanat istasyonları: (y, x_le, chord, twist_deg_extra) dizileri, y artan."""
    kind = w.get("type", "delta")
    if kind not in WING_TYPES:
        raise ValueError(f"Bilinmeyen kanat tipi: {kind}")
    if kind == "custom":
        sec = np.array(w.get("sections") or DEFAULT_CUSTOM_SECTIONS, dtype=float)
        if sec.ndim != 2 or sec.shape[1] < 3 or len(sec) < 2:
            raise ValueError("Özel kesit tablosunda en az 2 satır ve y, x_le, veter sütunları olmalı")
        if sec.shape[1] == 3:
            sec = np.column_stack([sec, np.zeros(len(sec))])
        sec = sec[np.argsort(sec[:, 0])]
        if abs(sec[0, 0]) > 1e-9:
            raise ValueError("İlk kesit kökte (y = 0) olmalı")
        if np.any(np.diff(sec[:, 0]) <= 0):
            raise ValueError("Kesitlerin y değerleri farklı olmalı")
        if np.any(sec[:-1, 2] <= 0) or sec[-1, 2] < 0:
            raise ValueError("Veter değerleri pozitif olmalı")
        return sec[:, 0], sec[:, 1], sec[:, 2], sec[:, 3]

    cr = _get(w, "root_chord")
    b = _get(w, "span")
    s = 0.5 * b
    if cr <= 0 or b <= 0:
        raise ValueError("Kök veteri ve açıklık pozitif olmalı")
    zero2 = np.zeros(2)

    if kind in ("delta", "cropped_delta"):
        lam = _get(w, "taper_ratio")
        tl = math.tan(math.radians(_get(w, "le_sweep_deg")))
        return np.array([0.0, s]), np.array([0.0, s * tl]), np.array([cr, lam * cr]), zero2

    if kind == "double_delta":
        lam = _get(w, "taper_ratio")
        yk = _get(w, "kink_eta") * s
        ti = math.tan(math.radians(_get(w, "inner_sweep_deg")))
        to = math.tan(math.radians(_get(w, "outer_sweep_deg")))
        xk = yk * ti
        xt = xk + (s - yk) * to
        ct = lam * cr
        # firar kenarı kökten uca düz çizgi
        xte_k = cr + (xt + ct - cr) * yk / s
        ck = xte_k - xk
        if ck <= 0:
            raise ValueError("Kırılma noktasında veter negatif: iç ok açısını veya kırılma konumunu azaltın")
        return np.array([0.0, yk, s]), np.array([0.0, xk, xt]), np.array([cr, ck, ct]), np.zeros(3)

    if kind in ("trapezoidal", "rectangular"):
        lam = 1.0 if kind == "rectangular" else _get(w, "taper_ratio")
        sweep = 0.0 if kind == "rectangular" else _get(w, "sweep_deg")
        ref = 0.0 if kind == "rectangular" else _get(w, "sweep_ref")
        ct = lam * cr
        xt = ref * cr + s * math.tan(math.radians(sweep)) - ref * ct
        return np.array([0.0, s]), np.array([0.0, xt]), np.array([cr, ct]), zero2

    if kind == "elliptical":
        sweep = math.tan(math.radians(_get(w, "sweep_deg")))
        eta = np.sin(np.linspace(0.0, 0.5 * np.pi, 41))
        y = eta * s
        c = cr * np.sqrt(np.clip(1.0 - eta**2, 0.0, 1.0))
        xle = 0.25 * cr - 0.25 * c + y * sweep
        return y, xle, c, np.zeros_like(y)

    raise ValueError(kind)
