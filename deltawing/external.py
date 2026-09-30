"""Harici geometri (STL / OBJ) içe aktarma, yönlendirme ve referans büyüklükleri.

Kullanıcının CAD'den getirdiği herhangi bir gövde (kanat, uçak, İHA, füze ...)
aynı OpenFOAM akışıyla analiz edilir. Bu modül:
  - ASCII / binary STL ve OBJ okur,
  - birim ölçekleme (mm, cm, in -> m) ve eksen yönlendirmesi uygular
    (hangi model ekseni akış yönünde, hangisi yukarı),
  - planform alanı, ön alan, ıslak alan, hacim, sızdırmazlık gibi bilgileri
    hesaplayıp CFD için referans alan / uzunluk önerir.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001, "in": 0.0254, "ft": 0.3048}
AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0),
        "+z": (0, 0, 1), "-z": (0, 0, -1)}


# --------------------------------------------------------------------------- okuma


def _read_stl(data: bytes):
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * n == len(data):
            rec = np.frombuffer(data[84:], dtype=np.dtype([
                ("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=n)
            tri_pts = rec["v"].astype(float)
            return _dedupe(tri_pts.reshape(-1, 3))
    text = data.decode("latin-1")
    vals = [line.split()[1:4] for line in text.splitlines() if line.strip().startswith("vertex")]
    if not vals:
        raise ValueError("STL dosyasında üçgen bulunamadı")
    return _dedupe(np.array(vals, dtype=float))


def _dedupe(pts: np.ndarray):
    """Üçgen köşe listesinden (3N,3) paylaşımlı köşe + üçgen indeksleri."""
    scale = max(np.ptp(pts, axis=0).max(), 1e-12)
    key = np.round(pts / scale * 1e9).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    verts = np.zeros((len(uniq), 3))
    verts[inv.ravel()] = pts
    tris = inv.reshape(-1, 3)
    good = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    return verts, tris[good]


def _read_obj(text: str):
    v, f = [], []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            v.append([float(x) for x in parts[1:4]])
        elif parts[0] == "f":
            idx = [int(p.split("/")[0]) for p in parts[1:]]
            idx = [i - 1 if i > 0 else len(v) + i for i in idx]
            for k in range(1, len(idx) - 1):  # çokgeni yelpaze ile üçgenle
                f.append([idx[0], idx[k], idx[k + 1]])
    if not f:
        raise ValueError("OBJ dosyasında yüz bulunamadı")
    return np.array(v, dtype=float), np.array(f, dtype=np.int64)


def load_mesh(path: str | Path):
    path = Path(path)
    data = path.read_bytes()
    if path.suffix.lower() == ".obj":
        return _read_obj(data.decode("latin-1"))
    return _read_stl(data)


# --------------------------------------------------------------------------- dönüşüm


def orientation_matrix(forward: str, up: str) -> np.ndarray:
    """Model eksenlerini CFD eksenlerine (x = akış yönü, z = yukarı) döndüren matris."""
    f = np.array(AXES[forward], dtype=float)
    u = np.array(AXES[up], dtype=float)
    if abs(f @ u) > 0.5:
        raise ValueError("Akış yönü ve yukarı ekseni birbirine dik olmalı")
    side = np.cross(u, f)  # CFD y ekseni (sağ kanat), sağ el kuralı: y = z × x
    return np.vstack([f, side, u])


@dataclass
class ExternalTransform:
    unit: str = "m"
    forward: str = "+x"   # model ekseni -> akış yönü (+x CFD)
    up: str = "+z"        # model ekseni -> yukarı (+z CFD)
    center: bool = True   # burnu x=0'a, y merkezini 0'a, z merkezini 0'a taşı
    symmetric: bool = False  # y=0 düzleminde yarım model (gövde simetrikse ~2x hızlı)
    rotate_deg: list = field(default_factory=lambda: [0.0, 0.0, 0.0])  # ek dönüş (x, y, z)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ExternalTransform":
        d = d or {}
        return cls(**{k: d[k] for k in ("unit", "forward", "up", "center", "symmetric", "rotate_deg") if k in d})


def _rot(axis: int, deg: float) -> np.ndarray:
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    m = np.eye(3)
    i, j = [(1, 2), (2, 0), (0, 1)][axis]
    m[i, i], m[i, j], m[j, i], m[j, j] = c, -s, s, c
    return m


def transform(verts: np.ndarray, t: ExternalTransform) -> np.ndarray:
    v = verts * UNITS[t.unit]
    v = v @ orientation_matrix(t.forward, t.up).T
    for ax, deg in enumerate(t.rotate_deg or [0, 0, 0]):
        if deg:
            v = v @ _rot(ax, float(deg)).T
    if t.center:
        lo, hi = v.min(axis=0), v.max(axis=0)
        v = v - np.array([lo[0], 0.5 * (lo[1] + hi[1]), 0.5 * (lo[2] + hi[2])])
    return v


# --------------------------------------------------------------------------- analiz


def projected_area(verts: np.ndarray, tris: np.ndarray, drop_axis: int, res: int = 1200) -> float:
    """Üçgenlerin bir düzleme izdüşüm alanı (kaplanan bölge; üst üste binmeler bir kez sayılır)."""
    from PIL import Image, ImageDraw

    keep = [i for i in range(3) if i != drop_axis]
    p = verts[:, keep]
    lo, hi = p.min(axis=0), p.max(axis=0)
    ext = np.maximum(hi - lo, 1e-12)
    h = ext.max() / res
    nx, ny = int(np.ceil(ext[0] / h)) + 3, int(np.ceil(ext[1] / h)) + 3
    img = Image.new("1", (nx, ny), 0)
    draw = ImageDraw.Draw(img)
    tp = (p[tris] - lo) / h + 1.0
    for tri in tp:
        draw.polygon([tuple(tri[0]), tuple(tri[1]), tuple(tri[2])], fill=1)
    # PIL kenar piksellerini dolu sayar; yarım piksellik kenar payını düzelt
    filled = np.asarray(img, dtype=bool)
    n = filled.sum()
    from PIL import ImageFilter
    edge = n - np.asarray(img.filter(ImageFilter.MinFilter(3)), dtype=bool).sum()
    return float((n - 0.5 * edge) * h * h)


def edge_stats(tris: np.ndarray) -> dict:
    edges = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    edges.sort(axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return {"open_edges": int(np.sum(counts == 1)), "nonmanifold_edges": int(np.sum(counts > 2))}


def mesh_metrics(verts: np.ndarray, tris: np.ndarray) -> dict:
    p = verts[tris]
    cr = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    wet = 0.5 * float(np.linalg.norm(cr, axis=1).sum())
    vol = float(np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6.0)
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    es = edge_stats(tris)
    return {
        "n_vertices": int(len(verts)),
        "n_triangles": int(len(tris)),
        "bbox_min": lo.tolist(),
        "bbox_max": hi.tolist(),
        "length_x_m": float(hi[0] - lo[0]),
        "span_y_m": float(hi[1] - lo[1]),
        "height_z_m": float(hi[2] - lo[2]),
        "wetted_area_m2": wet,
        "volume_m3": abs(vol),
        "closed": es["open_edges"] == 0 and es["nonmanifold_edges"] == 0,
        **es,
        "planform_area_m2": projected_area(verts, tris, 2),
        "frontal_area_m2": projected_area(verts, tris, 0),
    }


@dataclass
class Body:
    """CFD'ye verilecek genel gövde tanımı (kanat veya harici geometri)."""

    verts: np.ndarray        # CFD koordinatlarında (x akış, z yukarı), m
    tris: np.ndarray
    aref: float              # tam gövde referans alanı, m^2
    lref: float              # referans uzunluk (moment), m
    cofr: np.ndarray         # moment referans noktası
    symmetric: bool          # True: yalnızca y >= 0 çözülür, kuvvetler x2
    char_length: float       # alan boyutlandırma ölçeği, m
    info: dict = field(default_factory=dict)
    name: str = "wing"


def body_from_wing(wing, cfg: dict) -> Body:
    g = cfg["geometry"]
    ext = 0.02 * wing.root_chord  # kök kesiti simetri düzlemini kanadın içinde kesecek
    v, t = wing.surface_mesh(int(g["n_span"]), float(g["min_tip_chord_ratio"]), ext)
    cofr = np.array([wing.mac_x_le + 0.25 * wing.mac, 0.0, 0.0])
    return Body(v, t, wing.area, wing.mac, cofr, True, wing.root_chord,
                {"source": "wing", **wing.summary()})


def body_from_external(path: str | Path, t: ExternalTransform, aref: float | None = None,
                       lref: float | None = None, cofr=None) -> tuple[Body, dict]:
    verts, tris = load_mesh(path)
    v = transform(verts, t)
    m = mesh_metrics(v, tris)
    aref = float(aref) if aref else m["planform_area_m2"]
    lref = float(lref) if lref else m["length_x_m"]
    if cofr is None:
        lo = np.array(m["bbox_min"])
        cofr = np.array([lo[0] + 0.25 * m["length_x_m"], 0.0, 0.0])
    if t.symmetric and abs(0.5 * (m["bbox_min"][1] + m["bbox_max"][1])) > 0.05 * m["span_y_m"]:
        raise ValueError("Yarım model için gövde y = 0 düzlemine göre simetrik ve ortalanmış olmalı")
    body = Body(v, tris, aref, lref, np.asarray(cofr, dtype=float), bool(t.symmetric),
                max(m["length_x_m"], 1e-3), {"source": "external", "file": Path(path).name, **m})
    return body, m


def decimate_for_view(verts: np.ndarray, tris: np.ndarray, max_tris: int = 60000):
    """Tarayıcıda gösterim için üçgen sayısını sınırla (basit örnekleme)."""
    if len(tris) <= max_tris:
        return verts, tris
    idx = np.linspace(0, len(tris) - 1, max_tris).astype(int)
    t = tris[idx]
    used, inv = np.unique(t, return_inverse=True)
    return verts[used], inv.reshape(-1, 3)
