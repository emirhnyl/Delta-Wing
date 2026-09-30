"""Parametrik kanat profili (airfoil) tanımları.

Desteklenen tipler
------------------
- ``naca4``  : NACA 4 haneli seri. ``code: "0008"`` veya ``camber`` / ``camber_pos`` /
               ``thickness`` alanlarıyla (optimizasyon için sürekli değişkenler).
- ``cst``    : Kulfan CST (Class-Shape Transformation). ``upper`` ve ``lower``
               ağırlık listeleri ile. Optimizasyon için en esnek parametrizasyon.
- ``file``   : Selig formatında ``.dat`` dosyası (x, y koordinatları).

Tüm profiller birim veter (0 <= x <= 1) üzerinde, ortak bir x dağılımında
üst ve alt yüzey koordinatları olarak üretilir. Firar kenarı (TE) kapalıdır,
böylece üretilen 3B yüzey su geçirmez (watertight) olur.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb
from pathlib import Path

import numpy as np


def cosine_spacing(n: int) -> np.ndarray:
    """Hücum ve firar kenarında sıklaşan n+1 noktalı x dağılımı (0..1)."""
    beta = np.linspace(0.0, np.pi, n + 1)
    return 0.5 * (1.0 - np.cos(beta))


@dataclass
class AirfoilSection:
    """Ortak x dağılımında üst/alt yüzey ve kamber çizgisi."""

    x: np.ndarray        # (n+1,) 0 -> 1
    y_upper: np.ndarray  # (n+1,)
    y_lower: np.ndarray  # (n+1,)

    @property
    def camber(self) -> np.ndarray:
        return 0.5 * (self.y_upper + self.y_lower)

    @property
    def thickness(self) -> np.ndarray:
        return self.y_upper - self.y_lower

    @property
    def max_thickness(self) -> float:
        return float(self.thickness.max())

    @property
    def max_thickness_pos(self) -> float:
        return float(self.x[np.argmax(self.thickness)])

    @property
    def area(self) -> float:
        """Birim veterli kesit alanı (hacim hesabı için)."""
        return float(np.trapezoid(self.thickness, self.x))

    def camber_slope(self, xq: np.ndarray) -> np.ndarray:
        dz = np.gradient(self.camber, self.x)
        return np.interp(xq, self.x, dz)

    def camber_at(self, xq: np.ndarray) -> np.ndarray:
        return np.interp(xq, self.x, self.camber)

    def blend(self, other: "AirfoilSection", w: float) -> "AirfoilSection":
        """Doğrusal karışım: w=0 -> self, w=1 -> other (aynı x dağılımı şart)."""
        return AirfoilSection(
            self.x,
            (1.0 - w) * self.y_upper + w * other.y_upper,
            (1.0 - w) * self.y_lower + w * other.y_lower,
        )


# --------------------------------------------------------------------------- NACA 4


def parse_naca4(code: str) -> tuple[float, float, float]:
    code = str(code).strip().upper().replace("NACA", "").strip()
    if len(code) != 4 or not code.isdigit():
        raise ValueError(f"Geçersiz NACA 4 haneli kod: {code!r}")
    return int(code[0]) / 100.0, int(code[1]) / 10.0, int(code[2:]) / 100.0


def naca4(x: np.ndarray, camber: float, camber_pos: float, thickness: float) -> AirfoilSection:
    """NACA 4 haneli profil (kapalı firar kenarı: a4 = -0.1036)."""
    t = thickness
    yt = 5.0 * t * (
        0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4
    )
    m, p = camber, camber_pos
    yc = np.zeros_like(x)
    dyc = np.zeros_like(x)
    if m > 0.0 and 0.0 < p < 1.0:
        fwd = x < p
        yc[fwd] = m / p**2 * (2 * p * x[fwd] - x[fwd] ** 2)
        dyc[fwd] = 2 * m / p**2 * (p - x[fwd])
        aft = ~fwd
        yc[aft] = m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x[aft] - x[aft] ** 2)
        dyc[aft] = 2 * m / (1 - p) ** 2 * (p - x[aft])
    th = np.arctan(dyc)
    # Kalınlık kamber çizgisine dik uygulanır; x kaymasını ortak dağılıma geri
    # enterpole ederek üst/alt yüzeyi aynı x noktalarında tutuyoruz.
    xu, yu = x - yt * np.sin(th), yc + yt * np.cos(th)
    xl, yl = x + yt * np.sin(th), yc - yt * np.cos(th)
    y_upper = np.interp(x, xu, yu)
    y_lower = np.interp(x, xl, yl)
    y_upper[0] = y_lower[0] = 0.0
    y_upper[-1] = y_lower[-1] = yc[-1]
    return AirfoilSection(x, y_upper, y_lower)


# --------------------------------------------------------------------------- CST


def _cst_surface(x: np.ndarray, weights, te: float, n1=0.5, n2=1.0) -> np.ndarray:
    w = np.asarray(weights, dtype=float)
    n = len(w) - 1
    cls = x**n1 * (1.0 - x) ** n2
    shape = sum(w[i] * comb(n, i) * x**i * (1.0 - x) ** (n - i) for i in range(n + 1))
    return cls * shape + x * te


def cst(x: np.ndarray, upper, lower, te_thickness: float = 0.0) -> AirfoilSection:
    """Kulfan CST profili. ``lower`` ağırlıkları genelde negatiftir."""
    yu = _cst_surface(x, upper, 0.5 * te_thickness)
    yl = _cst_surface(x, lower, -0.5 * te_thickness)
    return AirfoilSection(x, yu, yl)


# --------------------------------------------------------------------------- DAT file


def from_dat(path: str | Path, x: np.ndarray) -> AirfoilSection:
    """Selig formatlı dosya (TE -> üst -> LE -> alt -> TE) okuyup x'e enterpole eder."""
    pts = []
    for line in Path(path).read_text().splitlines():
        parts = line.replace(",", " ").split()
        if len(parts) < 2:
            continue
        try:
            pts.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue  # başlık satırı
    xy = np.array(pts)
    i_le = int(np.argmin(xy[:, 0]))
    up, lo = xy[: i_le + 1][::-1], xy[i_le:]
    x0, c = xy[i_le, 0], xy[:, 0].max() - xy[i_le, 0]
    yu = np.interp(x, (up[:, 0] - x0) / c, up[:, 1] / c)
    yl = np.interp(x, (lo[:, 0] - x0) / c, lo[:, 1] / c)
    te = 0.5 * (yu[-1] + yl[-1])
    yu[-1] = yl[-1] = te  # 3B yüzeyin kapalı olması için TE'yi kapat
    return AirfoilSection(x, yu, yl)


# --------------------------------------------------------------------------- factory


def make_airfoil(spec: dict, n_points: int = 60, base_dir: Path | None = None) -> AirfoilSection:
    """Konfigürasyon sözlüğünden profil üretir."""
    x = cosine_spacing(n_points)
    kind = spec.get("type", "naca4").lower()
    if kind == "naca4":
        if "code" in spec and not any(k in spec for k in ("camber", "camber_pos", "thickness")):
            m, p, t = parse_naca4(spec["code"])
        else:
            m0, p0, t0 = parse_naca4(spec.get("code", "0008"))
            m = float(spec.get("camber", m0))
            p = float(spec.get("camber_pos", p0 if p0 > 0 else 0.4))
            t = float(spec.get("thickness", t0))
        if not 0.005 <= t <= 0.40:
            raise ValueError(f"NACA kalınlığı aralık dışı: {t}")
        return naca4(x, m, p, t)
    if kind == "cst":
        return cst(x, spec["upper"], spec["lower"], float(spec.get("te_thickness", 0.0)))
    if kind == "file":
        path = Path(spec["path"])
        if base_dir is not None and not path.is_absolute():
            path = base_dir / path
        return from_dat(path, x)
    raise ValueError(f"Bilinmeyen airfoil tipi: {kind}")
