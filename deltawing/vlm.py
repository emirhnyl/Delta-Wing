"""Hızlı 3B aerodinamik çözücü: Vortex Lattice Method (VLM) + Polhamus + viskoz direnç.

Bu modül RANS CFD'nin yerini tutmaz; optimizasyon döngüsünde binlerce tasarımı
saniyeler içinde taramak ve iyi adayları OpenFOAM'a göndermek için kullanılır.

Modeller
--------
- Potansiyel kaldırma: kamber yüzeyinde at nalı (horseshoe) girdap kafesi,
  y = 0 düzleminde ayna görüntüsü ile yarı model.
- İndüklenmiş direnç: Trefftz düzlemi analizi.
- Delta kanat girdap kaldırması: Polhamus hücum kenarı emme analojisi
      CL = Kp sin(a) cos^2(a) + Kv sin^2(a) cos(a)
  Girdap rejiminde hücum kenarı emmesi kaybolur: CDi = CL tan(a).
  Girdap patlaması (vortex breakdown) ve stall modellenmez (a < ~20-25 deg geçerli).
- Sıfır-kaldırma (sürtünme + form) direnci: türbülanslı düz levha Cf ve
  Raymer form faktörü ile bileşen toplama yöntemi.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .geometry import DeltaWing

FOUR_PI = 4.0 * np.pi


def _segment_velocity(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Birim şiddetli düz girdap segmentlerinin (a->b) p noktalarında indüklediği hız.

    p: (N,3), a,b: (M,3) -> (N,M,3)   [Katz & Plotkin, Denk. 10.115]
    """
    r1 = p[:, None, :] - a[None, :, :]
    r2 = p[:, None, :] - b[None, :, :]
    r0 = (b - a)[None, :, :]
    cr = np.cross(r1, r2)
    cr2 = np.einsum("ijk,ijk->ij", cr, cr)
    n1 = np.linalg.norm(r1, axis=2)
    n2 = np.linalg.norm(r2, axis=2)
    scale = np.linalg.norm(r0, axis=2) ** 2
    ok = (cr2 > 1e-10 * scale) & (n1 > 1e-12) & (n2 > 1e-12)
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.einsum("ijk,ijk->ij", r0, r1 / n1[..., None] - r2 / n2[..., None]) / (FOUR_PI * cr2)
    k = np.where(ok, k, 0.0)
    return cr * k[..., None]


def _horseshoe_velocity(p, a, b, far):
    """∞ -> a -> b -> ∞ at nalı girdabı (bacaklar +x yönünde)."""
    fa = a + np.array([far, 0.0, 0.0])
    fb = b + np.array([far, 0.0, 0.0])
    return _segment_velocity(p, fa, a) + _segment_velocity(p, a, b) + _segment_velocity(p, b, fb)


def _mirror(v):
    m = v.copy()
    m[:, 1] *= -1.0
    return m


@dataclass
class Lattice:
    a: np.ndarray       # bağlı girdap başlangıcı (M,3)
    b: np.ndarray       # bağlı girdap sonu (M,3)
    cp: np.ndarray      # kontrol noktaları (M,3)
    normal: np.ndarray  # birim normaller (M,3)
    y_edges: np.ndarray
    n_chord: int
    n_span: int


def build_lattice(wing: DeltaWing, n_chord: int = 12, n_span: int = 24) -> Lattice:
    # açıklıkta uca doğru sıklaşan dağılım
    y_e = wing.semi_span * np.sin(np.linspace(0.0, 0.5 * np.pi, n_span + 1))
    xi_e = np.linspace(0.0, 1.0, n_chord + 1)

    def surf(y, xi):
        sec = wing.section(y)
        xi = np.atleast_1d(xi)
        px, py, pz = wing.section_points(y, xi, sec.camber_at(xi))
        return np.column_stack([px, py, pz])

    A, B, CP, N = [], [], [], []
    for j in range(n_span):
        y0, y1 = y_e[j], y_e[j + 1]
        ym = 0.5 * (y0 + y1)
        for i in range(n_chord):
            x0, x1 = xi_e[i], xi_e[i + 1]
            xq = x0 + 0.25 * (x1 - x0)
            A.append(surf(y0, xq)[0])
            B.append(surf(y1, xq)[0])
            CP.append(surf(ym, x0 + 0.75 * (x1 - x0))[0])
            p00, p10 = surf(y0, x0)[0], surf(y0, x1)[0]
            p01, p11 = surf(y1, x0)[0], surf(y1, x1)[0]
            n = np.cross(p11 - p00, p01 - p10)
            if n[2] < 0:
                n = -n
            N.append(n / np.linalg.norm(n))
    return Lattice(np.array(A), np.array(B), np.array(CP), np.array(N), y_e, n_chord, n_span)


@dataclass
class PotentialResult:
    alpha_deg: float
    CL: float
    CDi: float
    gamma: np.ndarray
    span_load: np.ndarray  # şerit başına toplam sirkülasyon


def solve_potential(wing: DeltaWing, lat: Lattice, alpha_deg: float, aic=None) -> PotentialResult:
    al = np.radians(alpha_deg)
    vinf = np.array([np.cos(al), 0.0, np.sin(al)])
    far = 1000.0 * wing.span
    if aic is None:
        aic = _aic(lat, far)
    rhs = -lat.normal @ vinf
    gamma = np.linalg.solve(aic, rhs)

    # Kutta-Joukowski ile kaldırma (bağlı segment orta noktalarında yerel hız)
    mid = 0.5 * (lat.a + lat.b)
    v = vinf + _induced(mid, lat, far, gamma)
    dl = lat.b - lat.a
    f = gamma[:, None] * np.cross(v, dl)          # rho=1, V=1
    lift_dir = np.array([-np.sin(al), 0.0, np.cos(al)])
    s_half = 0.5 * wing.area
    CL = float(f.sum(axis=0) @ lift_dir) / (0.5 * s_half)

    # Trefftz düzlemi indüklenmiş direnci
    g_strip = gamma.reshape(lat.n_span, lat.n_chord).sum(axis=1)
    y_e = lat.y_edges
    g_ext = np.concatenate([[g_strip[0]], g_strip, [0.0]])
    gam_tr = g_ext[:-1] - g_ext[1:]               # kenar başına +x yönlü şiddet
    y_all = np.concatenate([y_e, -y_e])
    g_all = np.concatenate([gam_tr, -gam_tr])
    yc = 0.5 * (y_e[:-1] + y_e[1:])
    w = (g_all[None, :] / (2 * np.pi * (yc[:, None] - y_all[None, :]))).sum(axis=1)
    dy = np.diff(y_e)
    Di = -np.sum(g_strip * w * dy)                # tam kanat, rho=1, V=1
    CDi = float(Di / (0.5 * wing.area))
    return PotentialResult(alpha_deg, CL, CDi, gamma, g_strip)


def _aic(lat: Lattice, far: float) -> np.ndarray:
    v = _horseshoe_velocity(lat.cp, lat.a, lat.b, far)
    v += _horseshoe_velocity(lat.cp, _mirror(lat.b), _mirror(lat.a), far)
    return np.einsum("ijk,ik->ij", v, lat.normal)


def _induced(p, lat: Lattice, far: float, gamma: np.ndarray) -> np.ndarray:
    v = _horseshoe_velocity(p, lat.a, lat.b, far)
    v += _horseshoe_velocity(p, _mirror(lat.b), _mirror(lat.a), far)
    return np.einsum("ijk,j->ik", v, gamma)


# --------------------------------------------------------------------------- viskoz


def zero_lift_drag(wing: DeltaWing, velocity: float, nu: float, a_sound: float) -> dict:
    """Bileşen toplama yöntemi ile CD0 (sürtünme + form)."""
    re = velocity * wing.mac / nu
    mach = velocity / a_sound
    cf = 0.455 / (np.log10(re) ** 2.58 * (1 + 0.144 * mach**2) ** 0.65)
    tc = wing.mean_thickness_ratio()
    xm = 0.5 * (wing.root_airfoil.max_thickness_pos + wing.tip_airfoil.max_thickness_pos)
    xm = max(xm, 0.1)
    sweep_m = wing.sweep_at(xm)
    ff = (1 + 0.6 / xm * tc + 100 * tc**4) * 1.34 * max(mach, 0.05) ** 0.18 * np.cos(sweep_m) ** 0.28
    swet_ratio = 2.0 * (1 + 0.2 * tc) if tc > 0.05 else 2.003
    return {"Re_mac": re, "Mach": mach, "Cf": cf, "form_factor": ff,
            "Swet_Sref": swet_ratio, "CD0": cf * ff * swet_ratio}


# --------------------------------------------------------------------------- toplam


@dataclass
class AeroResult:
    alpha_deg: float
    CL: float
    CD: float
    CD0: float
    CDi: float
    CL_potential: float
    CL_vortex: float
    lift_N: float
    drag_N: float
    L_over_D: float
    q_Pa: float
    area_m2: float
    method: str = "vlm"

    def to_dict(self) -> dict:
        return asdict(self)


class QuickAero:
    """Bir kanat için kafesi ve AIC matrisini bir kez kurar, çok sayıda hücum açısını çözer."""

    def __init__(self, wing: DeltaWing, cfg: dict):
        self.wing = wing
        self.cfg = cfg
        v = cfg["vlm"]
        self.lat = build_lattice(wing, int(v["n_chord"]), int(v["n_span"]))
        self.far = 1000.0 * wing.span
        self.aic = _aic(self.lat, self.far)
        mode = str(v.get("vortex_lift", "auto")).lower()
        self.vortex = (wing.le_sweep_deg >= 45.0) if mode == "auto" else mode in ("on", "true", "1")
        f = cfg["flow"]
        self.visc = zero_lift_drag(wing, float(f["velocity"]), float(f["kinematic_viscosity"]),
                                   float(f["speed_of_sound"]))
        # Polhamus katsayıları
        r0 = solve_potential(wing, self.lat, 0.0, self.aic)
        r1 = solve_potential(wing, self.lat, 2.0, self.aic)
        r2 = solve_potential(wing, self.lat, 6.0, self.aic)
        self.Kp = (r1.CL - r0.CL) / np.radians(2.0)
        self.alpha0 = -r0.CL / self.Kp  # radyan
        self.Ki = (r2.CDi - r0.CDi) / max(r2.CL**2 - r0.CL**2, 1e-12)
        self.Kv = (self.Kp - self.Kp**2 * self.Ki) / np.cos(np.radians(wing.le_sweep_deg))

    def analyze(self, alpha_deg: float) -> AeroResult:
        f = self.cfg["flow"]
        q = 0.5 * float(f["density"]) * float(f["velocity"]) ** 2
        cd0 = self.visc["CD0"]
        if self.vortex:
            ae = np.radians(alpha_deg) - self.alpha0
            clp = self.Kp * np.sin(ae) * np.cos(ae) ** 2
            clv = self.Kv * np.sin(ae) * abs(np.sin(ae)) * np.cos(ae)
            cl = clp + clv
            cdi = cl * np.tan(ae)
        else:
            r = solve_potential(self.wing, self.lat, alpha_deg, self.aic)
            cl, clp, clv, cdi = r.CL, r.CL, 0.0, r.CDi
        cd = cd0 + cdi
        S = self.wing.area
        return AeroResult(
            alpha_deg=float(alpha_deg), CL=float(cl), CD=float(cd), CD0=float(cd0),
            CDi=float(cdi), CL_potential=float(clp), CL_vortex=float(clv),
            lift_N=float(cl * q * S), drag_N=float(cd * q * S),
            L_over_D=float(cl / cd) if cd > 0 else 0.0, q_Pa=q, area_m2=S,
            method="vlm+polhamus" if self.vortex else "vlm",
        )
