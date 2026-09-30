"""Parametrik kanat geometrisi ve su geçirmez STL üretimi.

Koordinat sistemi (gövde ekseni)
--------------------------------
x : kök veteri boyunca, hücum kenarından firar kenarına (akış yönü)
y : sağ kanat açıklığı yönü (y = 0 simetri düzlemi)
z : yukarı

Hücum açısı geometriye değil serbest akış vektörüne uygulanır; böylece aynı
ağ farklı hücum açılarında yeniden kullanılabilir.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .airfoil import AirfoilSection, make_airfoil


class Wing:
    """Genel kanat: planform istasyonları + kök/uç profili karışımı.

    Planform tipleri ``planforms.py`` içinde tanımlıdır (delta, çift delta, trapez,
    dikdörtgen, eliptik, özel). Tüm geometrik büyüklükler istasyonlardan sayısal
    olarak hesaplanır.
    """

    def __init__(self, ys, xle, chord, twist_extra_deg, root_airfoil: AirfoilSection,
                 tip_airfoil: AirfoilSection, twist_tip_deg: float = 0.0, twist_axis: float = 0.25,
                 dihedral_deg: float = 0.0, blend_exponent: float = 1.0, kind: str = "delta",
                 label: str = ""):
        self._y = np.asarray(ys, dtype=float)
        self._xle = np.asarray(xle, dtype=float)
        self._c = np.asarray(chord, dtype=float)
        self._tw = np.asarray(twist_extra_deg, dtype=float)
        self.root_airfoil = root_airfoil
        self.tip_airfoil = tip_airfoil
        self.twist_tip_deg = float(twist_tip_deg)
        self.twist_axis = float(twist_axis)
        self.dihedral_deg = float(dihedral_deg)
        self.blend_exponent = float(blend_exponent)
        self.kind = kind
        self.label = label or kind
        self.validate()
        self._integrals()

    # ------------------------------------------------------------------ build
    @classmethod
    def from_config(cls, cfg: dict) -> "Wing":
        from .planforms import WING_TYPES, stations

        w, a = cfg["wing"], cfg["airfoil"]
        base = cfg.get("_base_dir")
        base = Path(base) if base else None
        n = int(a.get("n_points", 60))
        kind = w.get("type", "delta")
        ys, xle, c, tw = stations(w)
        return cls(ys, xle, c, tw,
                   root_airfoil=make_airfoil(a["root"], n, base),
                   tip_airfoil=make_airfoil(a.get("tip", a["root"]), n, base),
                   twist_tip_deg=float(w.get("twist_tip_deg", 0.0)),
                   twist_axis=float(w.get("twist_axis", 0.25)),
                   dihedral_deg=float(w.get("dihedral_deg", 0.0)),
                   blend_exponent=float(a.get("blend_exponent", 1.0)),
                   kind=kind, label=WING_TYPES[kind]["label"])

    def validate(self) -> None:
        if len(self._y) < 2 or self._y[-1] <= 0:
            raise ValueError("Açıklık pozitif olmalı")
        if self._c[0] <= 0 or np.any(self._c < 0):
            raise ValueError("Veter değerleri pozitif olmalı")
        if np.any(np.diff(self._y) <= 0):
            raise ValueError("İstasyonlar açıklık boyunca artan sırada olmalı")

    def _integrals(self) -> None:
        y = self.fine_y(400)
        c = self.chord(y)
        xl = self.x_le(y)
        S2 = np.trapezoid(c, y)                      # yarı alan
        self._area = 2.0 * S2
        self._mac = np.trapezoid(c * c, y) / S2
        self._mac_y = np.trapezoid(c * y, y) / S2
        self._mac_xle = np.trapezoid(c * xl, y) / S2
        dx = np.gradient(xl, y)
        dc = np.gradient(c, y)
        self._tan_le = float(np.trapezoid(c * dx, y) / S2)
        self._tan_dc = float(np.trapezoid(c * dc, y) / S2)

    def fine_y(self, n: int) -> np.ndarray:
        """Kırılma istasyonlarını da içeren yoğun y dağılımı."""
        y = self.semi_span * np.sin(np.linspace(0.0, 0.5 * np.pi, n))
        return np.unique(np.concatenate([y, self._y]))

    def span_edges(self, n: int) -> np.ndarray:
        """Uca doğru sıklaşan n aralıklı dağılım; planform kırılmaları kenar olarak korunur."""
        y = self.semi_span * np.sin(np.linspace(0.0, 0.5 * np.pi, n + 1))
        kinks = self._y[1:-1]
        if len(kinks) and len(kinks) < 8:  # eliptik gibi yoğun tanımlı tiplerde gerek yok
            tol = 0.3 * np.min(np.diff(y))
            y = y[np.all(np.abs(y[:, None] - kinks[None, :]) > tol, axis=1)]
            y = np.sort(np.concatenate([y, kinks]))
        return y

    # ------------------------------------------------------------ planform
    @property
    def semi_span(self) -> float:
        return float(self._y[-1])

    @property
    def span(self) -> float:
        return 2.0 * self.semi_span

    @property
    def root_chord(self) -> float:
        return float(self._c[0])

    @property
    def tip_chord(self) -> float:
        return float(self._c[-1])

    @property
    def taper_ratio(self) -> float:
        return self.tip_chord / self.root_chord

    @property
    def area(self) -> float:
        """Tam kanat referans (planform) alanı, m^2."""
        return self._area

    @property
    def aspect_ratio(self) -> float:
        return self.span**2 / self.area

    @property
    def mac(self) -> float:
        return self._mac

    @property
    def mac_y(self) -> float:
        return self._mac_y

    @property
    def mac_x_le(self) -> float:
        return self._mac_xle

    @property
    def le_sweep_deg(self) -> float:
        """Alan ağırlıklı ortalama hücum kenarı ok açısı."""
        return float(np.degrees(np.arctan(self._tan_le)))

    @property
    def te_sweep_deg(self) -> float:
        return float(np.degrees(np.arctan(self._tan_le + self._tan_dc)))

    def sweep_at(self, frac: float) -> float:
        """Veterin ``frac`` oranındaki çizginin (alan ağırlıklı) ok açısı, radyan."""
        return float(np.arctan(self._tan_le + frac * self._tan_dc))

    def x_le(self, y):
        return np.interp(np.abs(y), self._y, self._xle)

    def chord(self, y, min_ratio: float = 0.0):
        c = np.interp(np.abs(y), self._y, self._c)
        return np.maximum(c, min_ratio * self.root_chord)

    def twist(self, y):
        eta = np.clip(np.abs(y) / self.semi_span, 0.0, 1.0)
        return np.radians(self.twist_tip_deg * eta + np.interp(np.abs(y), self._y, self._tw))

    def z_dihedral(self, y):
        return np.abs(y) * np.tan(np.radians(self.dihedral_deg))

    def section(self, y) -> AirfoilSection:
        eta = float(np.clip(abs(y) / self.semi_span, 0.0, 1.0))
        return self.root_airfoil.blend(self.tip_airfoil, eta**self.blend_exponent)

    def mean_thickness_ratio(self) -> float:
        return 0.5 * (self.root_airfoil.max_thickness + self.tip_airfoil.max_thickness)

    def section_points(self, y: float, xc: np.ndarray, zc: np.ndarray, min_ratio: float = 0.0):
        """Birim veterli (xc, zc) noktalarını y istasyonundaki 3B konuma taşır."""
        c = self.chord(y, min_ratio)
        th = self.twist(y)
        xa = self.twist_axis
        # burulma ekseni etrafında döndür (pozitif twist = burun yukarı)
        dx, dz = (xc - xa) * c, zc * c
        xr = xa * c + dx * np.cos(th) + dz * np.sin(th)
        zr = -dx * np.sin(th) + dz * np.cos(th)
        return self.x_le(y) + xr, np.full_like(xc, y, dtype=float), self.z_dihedral(y) + zr

    def volume(self, n: int = 80) -> float:
        """Tam kanat iç hacmi (m^3), yakıt/yapı kısıtları için."""
        ys = self.fine_y(n)
        a = [self.section(y).area * self.chord(y) ** 2 for y in ys]
        return 2.0 * float(np.trapezoid(a, ys))

    def planform_outline(self, n: int = 120):
        """Tam kanat planform çevresi (y, x) - görselleştirme için."""
        y = self.fine_y(n)
        le, te = self.x_le(y), self.x_le(y) + self.chord(y)
        ys = np.concatenate([-y[::-1], y, y[::-1], -y])
        xs = np.concatenate([le[::-1], le, te[::-1], te])
        return np.append(ys, ys[0]), np.append(xs, xs[0])

    def summary(self) -> dict:
        return {
            "type": self.kind,
            "span_m": self.span,
            "root_chord_m": self.root_chord,
            "tip_chord_m": self.tip_chord,
            "area_m2": self.area,
            "aspect_ratio": self.aspect_ratio,
            "mac_m": self.mac,
            "le_sweep_deg": self.le_sweep_deg,
            "te_sweep_deg": self.te_sweep_deg,
            "root_t_c": self.root_airfoil.max_thickness,
            "tip_t_c": self.tip_airfoil.max_thickness,
            "volume_m3": self.volume(),
        }


    # ------------------------------------------------------------ surface
    def surface_mesh(self, n_span: int = 40, min_tip_ratio: float = 0.02,
                     root_extension: float = 0.0, full: bool = False):
        """Yarı (veya ``full=True`` ile tam) kanadın kapalı üçgen yüzey ağı.

        ``root_extension`` > 0 ise kök kesiti y = -root_extension'a uzatılır:
        snappyHexMesh simetri düzlemini kanadın içinden temiz biçimde keser.
        Döndürür: (vertices (N,3), triangles (M,3) int)
        """
        # açıklıkta uca doğru sıklaşan dağılım
        ys = self.span_edges(n_span)
        x = self.root_airfoil.x
        n = len(x) - 1
        loops = []
        yst = list(ys)
        if full:
            yst = [-y for y in ys[::-1]] + yst[1:]
        elif root_extension > 0:
            yst = [-root_extension] + yst
        for y in yst:
            ye = abs(y) if full else max(y, 0.0)
            sec = self.section(ye)
            # döngü: TE -> üst -> LE -> alt -> (TE hariç)
            xc = np.concatenate([x[::-1], x[1:-1]])
            zc = np.concatenate([sec.y_upper[::-1], sec.y_lower[1:-1]])
            px, _, pz = self.section_points(ye, xc, zc, min_tip_ratio)
            loops.append(np.column_stack([px, np.full_like(px, y), pz]))
        m = 2 * n  # döngü başına nokta
        verts = np.vstack(loops)
        tris = []
        for j in range(len(loops) - 1):
            a0, b0 = j * m, (j + 1) * m
            for k in range(m):
                k1 = (k + 1) % m
                tris.append((a0 + k, a0 + k1, b0 + k1))
                tris.append((a0 + k, b0 + k1, b0 + k))

        def cap(offset: int, outward_sign: float):
            up = lambda i: offset + (n - i)        # noqa: E731
            lo = lambda i: offset + (n + i) % m    # noqa: E731
            cap_tris = []
            for i in range(n):
                quad = [up(i), up(i + 1), lo(i + 1), lo(i)]
                cap_tris += [(quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3])]
            out = []
            for t in cap_tris:
                if len(set(t)) < 3:
                    continue
                p = verts[list(t)]
                nrm = np.cross(p[1] - p[0], p[2] - p[0])
                if np.linalg.norm(nrm) < 1e-14:
                    continue
                out.append(t if nrm[1] * outward_sign > 0 else (t[0], t[2], t[1]))
            return out

        side = np.array(tris)
        # yan yüzey yönünü işaretli hacim ile kontrol et
        p = verts[side]
        vol = np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum()
        root_c = cap(0, -1.0)
        tip_c = cap((len(loops) - 1) * m, +1.0)
        allt = np.vstack([side, np.array(root_c), np.array(tip_c)])
        p = verts[allt]
        vol = np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6.0
        if vol < 0:
            side = side[:, [0, 2, 1]]
            allt = np.vstack([side, np.array(root_c), np.array(tip_c)])
        # dejenere (sıfır alanlı) üçgenleri at
        p = verts[allt]
        area = np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
        allt = allt[area > 1e-14 * self.root_chord**2]
        return verts, allt


DeltaWing = Wing  # geriye dönük uyumluluk


# --------------------------------------------------------------------------- STL


def write_stl(path: str | Path, verts: np.ndarray, tris: np.ndarray, name: str = "wing") -> Path:
    """ASCII STL yazar (katı adı OpenFOAM patch adı olur)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    p = verts[tris]
    nrm = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    lines = [f"solid {name}"]
    for (a, b, c), nv in zip(p, nrm):
        lines.append(f"  facet normal {nv[0]:.6e} {nv[1]:.6e} {nv[2]:.6e}")
        lines.append("    outer loop")
        for v in (a, b, c):
            lines.append(f"      vertex {v[0]:.8e} {v[1]:.8e} {v[2]:.8e}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    path.write_text("\n".join(lines) + "\n")
    return path


def mesh_is_closed(tris: np.ndarray) -> bool:
    """Her kenar tam olarak iki üçgen tarafından paylaşılıyorsa yüzey kapalıdır."""
    edges = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    edges.sort(axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return bool(np.all(counts == 2))


def mirror_full(verts: np.ndarray, tris: np.ndarray):
    """Yarı kanattan tam kanat (görselleştirme / tam model CFD için)."""
    mv = verts.copy()
    mv[:, 1] *= -1
    n = len(verts)
    return np.vstack([verts, mv]), np.vstack([tris, tris[:, [0, 2, 1]] + n])
