"""CFD akış görselleştirmesi: yüzey basıncı, kesit haritaları ve 3B akış çizgileri.

OpenFOAM çözümünü (ASCII polyMesh + p, U alanları) doğrudan okur; ParaView veya
OpenFOAM fonksiyon nesnesi gerektirmez. Her vaka için şunları üretir:

  viz/surface.json      gövde yüzeyi, üçgen başına Cp (tam gövde, simetri aynalanmış)
  viz/streamlines.json  3B akış çizgileri (nokta başına hız / U∞)
  viz/slices.json       kesit düzlemleri: Cp, |U|/U∞, toplam basınç Cp0, kesit içi akış çizgileri
  viz/*.png             rapor ve galeri için statik görüntüler

Tanımlar (sıkıştırılamaz, p kinematik, p∞ = 0):
  Cp  = p / (½ U∞²)
  Cp0 = (p + ½|U|²) / (½ U∞²)   → serbest akışta 1; girdap çekirdeği ve izde < 1
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .foamforces import _list_body, _read_body, latest_time, read_boundary, read_field


# --------------------------------------------------------------------------- okuma


def _ints(body: str) -> np.ndarray:
    return np.fromstring(body.replace("(", " ").replace(")", " "), dtype=np.int64, sep=" ")


def _floats(body: str) -> np.ndarray:
    return np.fromstring(body.replace("(", " ").replace(")", " "), dtype=float, sep=" ")


class FoamCase:
    """Tüm ağın vektörel geometrisi + son zaman adımındaki p, U."""

    def __init__(self, case: str | Path):
        t0 = time.time()
        self.case = Path(case)
        self.info = json.loads((self.case / "case_info.json").read_text())
        poly = self.case / "constant" / "polyMesh"
        n, body, _ = _list_body(_read_body(poly / "points"))
        self.points = _floats(body).reshape(n, 3)
        nf, body, _ = _list_body(_read_body(poly / "faces"))
        flat = _ints(body)
        sizes = np.empty(nf, dtype=np.int64)
        starts = np.empty(nf, dtype=np.int64)
        i = 0
        fl = flat.tolist()
        for k in range(nf):  # "n v1 .. vn" dizisi
            s = fl[i]
            sizes[k] = s
            starts[k] = i + 1
            i += s + 1
        self.face_sizes = sizes
        idx = np.repeat(starts, sizes) + (np.arange(sizes.sum()) - np.repeat(np.cumsum(sizes) - sizes, sizes))
        self.face_verts = flat[idx]                 # düz köşe listesi
        self.face_offsets = np.concatenate([[0], np.cumsum(sizes)])
        _, body, _ = _list_body(_read_body(poly / "owner"))
        self.owner = _ints(body)[:nf]
        _, body, _ = _list_body(_read_body(poly / "neighbour"))
        self.neighbour = _ints(body)
        self.n_internal = len(self.neighbour)
        self.ncells = int(max(self.owner.max(), self.neighbour.max())) + 1
        self.boundary = read_boundary(poly)
        self._face_geometry()
        self._cell_geometry()
        tdir = latest_time(self.case)
        self.time = tdir.name
        self.p, _ = read_field(tdir / "p", "wing", 1, self.ncells)
        self.U, _ = read_field(tdir / "U", "wing", 3, self.ncells)
        self.Uinf = float(self.info["velocity"])
        self.load_time = time.time() - t0

    def _face_geometry(self):
        fv, off, sz = self.face_verts, self.face_offsets, self.face_sizes
        p = self.points[fv]
        fid = np.repeat(np.arange(len(sz)), sz)
        c0 = np.add.reduceat(p, off[:-1], axis=0) / sz[:, None]
        nxt = np.arange(len(fv)) + 1
        nxt[off[1:] - 1] = off[:-1]                  # her yüzün son köşesi ilkine bağlanır
        tri = 0.5 * np.cross(p - c0[fid], p[nxt] - c0[fid])
        self.Sf = np.add.reduceat(tri, off[:-1], axis=0)
        self.Cf = c0

    def _cell_geometry(self):
        own, nei, ni = self.owner, self.neighbour, self.n_internal
        vol = np.zeros(self.ncells)
        dv = np.einsum("ij,ij->i", self.Cf, self.Sf) / 3.0
        np.add.at(vol, own, dv)
        np.add.at(vol, nei, -dv[:ni])
        mag = np.linalg.norm(self.Sf, axis=1)
        wsum = np.zeros(self.ncells)
        csum = np.zeros((self.ncells, 3))
        np.add.at(wsum, own, mag)
        np.add.at(wsum, nei, mag[:ni])
        np.add.at(csum, own, self.Cf * mag[:, None])
        np.add.at(csum, nei, self.Cf[:ni] * mag[:ni, None])
        self.C = csum / wsum[:, None]
        self.V = np.abs(vol)
        self.h = np.cbrt(self.V)

    # ---------------------------------------------------------------- türetilmiş
    @property
    def q(self) -> float:
        return 0.5 * self.Uinf**2

    def cp(self):
        return self.p / self.q

    def cp0(self):
        return (self.p + 0.5 * np.einsum("ij,ij->i", self.U, self.U)) / self.q

    def patch_faces(self, name="wing"):
        start, n = self.boundary[name]
        return np.arange(start, start + n)

    def tree(self):
        if not hasattr(self, "_tree"):
            from scipy.spatial import cKDTree
            self._tree = cKDTree(self.C)
        return self._tree

    def sample(self, pts: np.ndarray, fields: dict[str, np.ndarray], k: int = 6):
        """Ters mesafe ağırlıklı örnekleme; katı gövde içindeki noktalar NaN."""
        d, i = self.tree().query(pts, k=k)
        w = 1.0 / np.maximum(d, 1e-12) ** 2
        w /= w.sum(axis=1, keepdims=True)
        # en yakın hücre merkezi komşu hücre boyutlarından bile uzaksa nokta katı gövdenin içindedir
        inside = d[:, 0] > 0.9 * self.h[i].max(axis=1)
        out = {}
        for name, f in fields.items():
            v = (f[i] * (w[..., None] if f.ndim == 2 else w)).sum(axis=1)
            v[inside] = np.nan
            out[name] = v
        return out, inside


# --------------------------------------------------------------------------- yüzey


def surface_data(fc: FoamCase, max_tris: int = 90000) -> dict:
    faces = fc.patch_faces("wing")
    cp = fc.cp()[fc.owner[faces]]
    tris, vals = [], []
    for f, c in zip(faces, cp):
        a, b = fc.face_offsets[f], fc.face_offsets[f + 1]
        v = fc.face_verts[a:b]
        for k in range(1, len(v) - 1):
            tris.append((v[0], v[k], v[k + 1]))
            vals.append(c)
    tris = np.array(tris)
    vals = np.array(vals)
    used, inv = np.unique(tris, return_inverse=True)
    verts = fc.points[used]
    tris = inv.reshape(-1, 3)
    if fc.info.get("symmetric", True):  # yarım model → tam gövde
        mv = verts.copy()
        mv[:, 1] *= -1
        n = len(verts)
        verts = np.vstack([verts, mv])
        tris = np.vstack([tris, tris[:, [0, 2, 1]] + n])
        vals = np.concatenate([vals, vals])
    if len(tris) > max_tris:
        sel = np.linspace(0, len(tris) - 1, max_tris).astype(int)
        tris, vals = tris[sel], vals[sel]
    lo, hi = np.percentile(vals, [1, 99])
    return {"x": verts[:, 0], "y": verts[:, 1], "z": verts[:, 2], "i": tris[:, 0], "j": tris[:, 1],
            "k": tris[:, 2], "cp": vals, "cp_range": [float(lo), float(hi)],
            "cp_min": float(vals.min()), "cp_max": float(vals.max())}


# --------------------------------------------------------------------------- geometri yardımcıları


def body_bounds(fc: FoamCase):
    faces = fc.patch_faces("wing")
    pts = fc.Cf[faces]
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    if fc.info.get("symmetric", True):
        lo[1] = -hi[1]
    return lo, hi


def body_outline(fc: FoamCase, axis: int, value: float) -> list:
    """Gövde yüzeyinin bir düzlemle kesişim segmentleri [[x0,y0,x1,y1], ...] (düzlem koordinatlarında)."""
    faces = fc.patch_faces("wing")
    keep = [i for i in range(3) if i != axis]
    segs = []
    for f in faces:
        a, b = fc.face_offsets[f], fc.face_offsets[f + 1]
        p = fc.points[fc.face_verts[a:b]]
        for sgn in ((1,) if not fc.info.get("symmetric", True) else (1, -1)):
            q = p.copy()
            q[:, 1] *= sgn
            d = q[:, axis] - value
            if d.min() > 0 or d.max() < 0:
                continue
            cut = []
            for k in range(len(q)):
                d0, d1 = d[k], d[(k + 1) % len(q)]
                if (d0 <= 0 < d1) or (d1 <= 0 < d0):
                    t = d0 / (d0 - d1)
                    x = q[k] + t * (q[(k + 1) % len(q)] - q[k])
                    cut.append(x[keep])
            if len(cut) >= 2:
                segs.append([float(cut[0][0]), float(cut[0][1]), float(cut[1][0]), float(cut[1][1])])
    return segs


# --------------------------------------------------------------------------- kesitler


def _plane_grid(axis: int, value: float, lo: np.ndarray, hi: np.ndarray, n_long: int = 200):
    keep = [i for i in range(3) if i != axis]
    a0, a1 = lo[keep[0]], hi[keep[0]]
    b0, b1 = lo[keep[1]], hi[keep[1]]
    ratio = (b1 - b0) / (a1 - a0)
    na = n_long if ratio <= 1 else max(20, int(n_long / ratio))
    nb = max(20, int(na * ratio))
    ua, ub = np.linspace(a0, a1, na), np.linspace(b0, b1, nb)
    A, Bm = np.meshgrid(ua, ub)                    # (nb, na)
    pts = np.zeros((A.size, 3))
    pts[:, axis] = value
    pts[:, keep[0]] = A.ravel()
    pts[:, keep[1]] = Bm.ravel()
    return ua, ub, pts, keep


def slice_data(fc: FoamCase, name: str, label: str, axis: int, value: float, lo, hi) -> dict:
    ua, ub, pts, keep = _plane_grid(axis, value, lo, hi)
    s, inside = fc.sample(pts, {"cp": fc.cp(), "cp0": fc.cp0(), "U": fc.U})
    shape = (len(ub), len(ua))
    U = s["U"]
    umag = np.linalg.norm(U, axis=1) / fc.Uinf
    ui = U[:, keep[0]].reshape(shape)
    vi = U[:, keep[1]].reshape(shape)
    lines = _stream2d(ua, ub, ui, vi)
    return {"name": name, "label": label, "axis": "xyz"[axis], "value": float(value),
            "a_label": "xyz"[keep[0]] + " [m]", "b_label": "xyz"[keep[1]] + " [m]",
            "a": ua, "b": ub, "cp": s["cp"].reshape(shape), "cp0": s["cp0"].reshape(shape),
            "umag": umag.reshape(shape), "streams": lines, "outline": body_outline(fc, axis, value)}


def _stream2d(ua, ub, u, v, density: float = 1.3) -> list:
    """matplotlib streamplot ile kesit içi akış çizgileri; [[x...],[y...]] listesi."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    uu = np.nan_to_num(u)
    vv = np.nan_to_num(v)
    if not np.any(uu) and not np.any(vv):
        return []
    fig, ax = plt.subplots()
    try:
        sp = ax.streamplot(ua, ub, uu, vv, density=density, linewidth=0.6, arrowsize=0.01)
        segs = sp.lines.get_segments()
    finally:
        plt.close(fig)
    if segs and all(len(sg) == 2 for sg in segs):
        # eski matplotlib: 2 noktalı segmentler → ardışık olanları birleştir
        lines, cur = [], None
        for sg in segs:
            if cur is not None and np.allclose(cur[-1], sg[0]):
                cur.append(sg[1])
            else:
                if cur is not None:
                    lines.append(cur)
                cur = [sg[0], sg[1]]
        if cur is not None:
            lines.append(cur)
    else:
        lines = list(segs)
    lines = [ln for ln in lines if len(ln) > 3]
    return [[[round(float(p[0]), 5) for p in ln], [round(float(p[1]), 5) for p in ln]] for ln in lines]


def standard_slices(fc: FoamCase) -> list[dict]:
    lo, hi = body_bounds(fc)
    L = float(hi[0] - lo[0])
    sym = fc.info.get("symmetric", True)
    semi = float(hi[1])
    zc = 0.5 * (lo[2] + hi[2])
    zpad = max(0.35 * L, 3 * (hi[2] - lo[2]))
    out = []
    # veter boyunca kesitler (x-z), açıklık oranlarında
    x_lo, x_hi = lo[0] - 0.3 * L, hi[0] + 0.6 * L
    for frac in (0.25, 0.5, 0.75):
        y = frac * semi if sym else 0.5 * (lo[1] + hi[1]) + frac * 0.5 * (hi[1] - lo[1])
        out.append(slice_data(fc, f"span{int(frac * 100)}", f"Veter kesiti · %{int(frac * 100)} yarı açıklık (y = {y:.3g} m)",
                              1, y, np.array([x_lo, y, zc - zpad]), np.array([x_hi, y, zc + zpad])))
    # çapraz akış kesitleri (y-z): gövde ortası, firar kenarı arkası, iz
    y_lo, y_hi = (-1.25 * semi, 1.25 * semi) if sym else (lo[1] - 0.25 * (hi[1] - lo[1]), hi[1] + 0.25 * (hi[1] - lo[1]))
    zz = max(0.45 * (y_hi - y_lo), 3 * (hi[2] - lo[2]))
    for name, x, lab in (("cross_mid", lo[0] + 0.6 * L, "Çapraz kesit · gövde %60"),
                         ("cross_te", hi[0] + 0.05 * L, "Çapraz kesit · firar kenarı arkası"),
                         ("cross_wake", hi[0] + 0.5 * L, "Çapraz kesit · iz (0.5 L arkada)")):
        s = slice_data(fc, name, f"{lab} (x = {x:.3g} m)", 0, x,
                       np.array([x, y_lo, zc - 0.5 * zz]), np.array([x, y_hi, zc + 0.5 * zz]))
        if sym:  # yarım modelin y<0 tarafı aynalama ile dolu gelir (örnekleme merkezleri y>=0)
            _mirror_slice_y(s)
        out.append(s)
    return out


def _mirror_slice_y(s: dict) -> None:
    """Yarım modelde y<0 bölgesini y>0 değerlerinin aynasıyla doldur (a ekseni = y)."""
    a = s["a"]
    neg = a < 0
    if not neg.any():
        return
    src = np.searchsorted(a, -a[neg])
    src = np.clip(src, 0, len(a) - 1)
    for k in ("cp", "cp0", "umag"):
        s[k][:, neg] = s[k][:, src]
    # kesit içi akış çizgilerini yeniden hesaplamak yerine y>0 çizgilerini aynala
    pos = [ln for ln in s["streams"] if np.mean(ln[0]) > 0]
    s["streams"] = pos + [[[-x for x in ln[0]], ln[1]] for ln in pos]


# --------------------------------------------------------------------------- 3B akış çizgileri


def streamlines_3d(fc: FoamCase, n_seed_y: int = 14, n_seed_z: int = 3, max_steps: int = 900) -> dict:
    from scipy.interpolate import RegularGridInterpolator

    lo, hi = body_bounds(fc)
    L = float(hi[0] - lo[0])
    sym = fc.info.get("symmetric", True)
    y0 = 0.0 if sym else lo[1] - 0.3 * (hi[1] - lo[1])
    y1 = hi[1] + 0.3 * max(L, hi[1] - lo[1]) * 0.5
    box_lo = np.array([lo[0] - 0.4 * L, y0, lo[2] - 0.45 * L])
    box_hi = np.array([hi[0] + 1.0 * L, y1, hi[2] + 0.45 * L])
    n = np.array([170, max(50, int(170 * (box_hi[1] - box_lo[1]) / (box_hi[0] - box_lo[0]))), 60])
    axes = [np.linspace(box_lo[d], box_hi[d], n[d]) for d in range(3)]
    G = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    s, inside = fc.sample(G, {"U": fc.U}, k=4)
    Ug = np.nan_to_num(s["U"]).reshape(*n, 3)
    interp = RegularGridInterpolator(axes, Ug, bounds_error=False, fill_value=0.0)

    # tohumlar: serbest akış yönünde geriye taşınmış hedef noktalar (akış α ile yükselir)
    al = np.radians(float(fc.info["alpha_deg"]))
    fdir = np.array([np.cos(al), 0.0, np.sin(al)])
    back = lambda pt, dist: np.asarray(pt, float) - dist * fdir  # noqa: E731
    seeds = []
    faces = fc.patch_faces("wing")
    Cfw = fc.Cf[faces]
    ymax = hi[1]
    ystations = np.linspace(0.06, 0.92, 12) * ymax if sym else np.linspace(lo[1], hi[1], 14)[1:-1]
    for yv in ystations:
        band = Cfw[np.abs(Cfw[:, 1] - yv) < 0.02 * L]
        if not len(band):
            continue
        nose = band[np.argmin(band[:, 0])]
        # hücum kenarının hemen üstü ve altı: üsttekiler girdaba sarılır, alttakiler alt yüzeyi izler
        for dz in (-0.01, 0.004, 0.012, 0.03):
            seeds.append(back(nose + np.array([0, 0, dz * L]), 0.05 * L))
    # genel görünüm için daha geniş bir tırmık
    zc = 0.5 * (lo[2] + hi[2])
    ys = np.linspace(0.05, 1.2, n_seed_y) * ymax if sym else np.linspace(lo[1] - 0.1 * (hi[1] - lo[1]), hi[1] + 0.1 * (hi[1] - lo[1]), n_seed_y)
    for yv in ys[::3]:
        for dz in (-0.08, 0.08):
            seeds.append(back([lo[0], yv, zc + dz * L], 0.3 * L))
    seeds = [sd for sd in seeds if np.all(sd > box_lo) and np.all(sd < box_hi)]
    P = np.array(seeds, dtype=float)
    ds = 0.008 * L
    paths = [[p.copy()] for p in P]
    speeds = [[float(np.linalg.norm(interp(p[None])[0]))] for p in P]
    alive = np.ones(len(P), dtype=bool)
    Uinf = fc.Uinf

    def vel(x):
        v = interp(x)
        m = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.maximum(m, 1e-9), m[:, 0]

    for _ in range(max_steps):
        idx = np.nonzero(alive)[0]
        if not len(idx):
            break
        x = P[idx]
        k1, m1 = vel(x)
        k2, _ = vel(x + 0.5 * ds * k1)
        k3, _ = vel(x + 0.5 * ds * k2)
        k4, _ = vel(x + ds * k3)
        xn = x + ds / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
        _, mn = vel(xn)
        out = np.any(xn < box_lo, axis=1) | np.any(xn > box_hi, axis=1) | (mn < 0.02 * Uinf)
        if sym:
            out |= xn[:, 1] < 0
        P[idx] = xn
        for j, ii in enumerate(idx):
            if out[j]:
                alive[ii] = False
            else:
                paths[ii].append(xn[j].copy())
                speeds[ii].append(float(mn[j]))
    lines = []
    for pth, sp in zip(paths, speeds):
        if len(pth) < 5:
            continue
        a = np.array(pth)[::2]
        v = (np.array(sp)[::2] / Uinf)
        lines.append({"x": a[:, 0], "y": a[:, 1], "z": a[:, 2], "s": v})
        if sym:
            lines.append({"x": a[:, 0], "y": -a[:, 1], "z": a[:, 2], "s": v})
    return {"lines": lines, "box": [box_lo, box_hi]}


# --------------------------------------------------------------------------- statik görüntüler


def render_images(fc: FoamCase, surf: dict, slices: list, stream: dict, out: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    from .plots import INK, INK2, SURFACE, _style

    alpha = fc.info["alpha_deg"]
    files = []
    x, y, z = surf["x"], surf["y"], surf["z"]
    tri = np.column_stack([surf["i"], surf["j"], surf["k"]])
    cp = surf["cp"]
    lo, hi = surf["cp_range"]
    m = max(abs(lo), abs(hi))

    # 1) yüzey Cp: üst ve alt görünüş
    p = np.column_stack([x, y, z])[tri]
    nz = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])[:, 2]
    fig, axs = plt.subplots(1, 2, figsize=(12, 5.4), facecolor=SURFACE)
    # yama yüz normalleri akış bölgesinin dışına, yani gövdenin içine bakar: üst yüzeyde nz < 0
    for ax, sel, ttl in ((axs[0], nz < 0, "Üst yüzey"), (axs[1], nz >= 0, "Alt yüzey")):
        if sel.any():
            tp = ax.tripcolor(y, x, tri[sel], facecolors=cp[sel], cmap="RdBu", vmin=-m, vmax=m, shading="flat")
        ax.set_aspect("equal")
        ax.invert_yaxis()
        ax.set_title(f"{ttl} · Cp", fontsize=11)
        ax.set_xlabel("y [m]")
        ax.set_ylabel("x [m] (akış ↓)")
        _style(ax)
        ax.grid(False)
    cb = fig.colorbar(tp, ax=axs, shrink=0.85, pad=0.02)
    cb.set_label("Cp (negatif = emme)", color=INK2)
    fig.suptitle(f"Yüzey basınç katsayısı · α = {alpha:g}°", color=INK)
    f = out / "surface_cp.png"
    fig.savefig(f, dpi=130, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    files.append(f.name)

    # 2) kesitler
    for s in slices:
        for key, cmap, lab, rng in (("cp", "RdBu", "Cp", None), ("cp0", "Blues_r", "Toplam basınç Cp0", (None, 1.02)),
                                    ("umag", "viridis", "|U| / U∞", None)):
            if key == "cp0" and not s["name"].startswith("cross"):
                continue
            if key == "umag" and s["name"].startswith("cross"):
                continue
            if key == "cp" and s["name"].startswith("cross"):
                continue
            fig, ax = plt.subplots(figsize=(9, 5.2), facecolor=SURFACE)
            Z = np.ma.masked_invalid(s[key])
            if key == "cp":
                mm = np.nanpercentile(np.abs(s[key]), 99)
                im = ax.pcolormesh(s["a"], s["b"], Z, cmap=cmap, vmin=-mm, vmax=mm, shading="auto")
            elif key == "cp0":
                im = ax.pcolormesh(s["a"], s["b"], Z, cmap=cmap, vmin=np.nanpercentile(s[key], 1), vmax=1.02, shading="auto")
            else:
                im = ax.pcolormesh(s["a"], s["b"], Z, cmap=cmap, shading="auto")
            segs = [np.column_stack(ln) for ln in s["streams"]]
            ax.add_collection(LineCollection(segs, colors="white" if key != "cp" else "#222", linewidths=0.6, alpha=0.75))
            ol = s["outline"]
            if ol:
                ax.add_collection(LineCollection([[(a[0], a[1]), (a[2], a[3])] for a in ol], colors=INK, linewidths=1.2))
            ax.set_aspect("equal")
            ax.set_xlim(s["a"][0], s["a"][-1])
            ax.set_ylim(s["b"][0], s["b"][-1])
            ax.set_xlabel(s["a_label"])
            ax.set_ylabel(s["b_label"])
            ax.set_title(f"{s['label']} · α = {alpha:g}°", fontsize=10)
            cb = fig.colorbar(im, ax=ax, shrink=0.9, pad=0.02)
            cb.set_label(lab, color=INK2)
            _style(ax)
            ax.grid(False)
            f = out / f"slice_{s['name']}_{key}.png"
            fig.savefig(f, dpi=130, facecolor=SURFACE, bbox_inches="tight")
            plt.close(fig)
            files.append(f.name)

    # 3) 3B akış çizgileri + yüzey
    fig = plt.figure(figsize=(11, 7), facecolor=SURFACE)
    ax = fig.add_subplot(projection="3d")
    sel = np.linspace(0, len(tri) - 1, min(len(tri), 12000)).astype(int)
    ax.plot_trisurf(x, y, z, triangles=tri[sel], color="#c9c8c2", linewidth=0, alpha=0.9, shade=True)
    import matplotlib.cm as cm
    import matplotlib.colors as mcolors
    smax = max((float(np.max(ln["s"])) for ln in stream["lines"]), default=1.5)
    norm = mcolors.Normalize(vmin=0.4, vmax=max(1.2, smax))
    for ln in stream["lines"]:
        pts = np.column_stack([ln["x"], ln["y"], ln["z"]])
        segs = np.stack([pts[:-1], pts[1:]], axis=1)
        from mpl_toolkits.mplot3d.art3d import Line3DCollection
        lc = Line3DCollection(segs, cmap="viridis", norm=norm, linewidths=0.9)
        lc.set_array(np.asarray(ln["s"][:-1]))
        ax.add_collection3d(lc)
    b0, b1 = stream["box"]
    r = 0.5 * max(b1 - b0)
    c = 0.5 * (b0 + b1)
    if fc.info.get("symmetric", True):
        c[1] = 0
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    ax.set_zlim(c[2] - 0.5 * r, c[2] + 0.5 * r)
    ax.set_box_aspect((1, 1, 0.5))
    ax.view_init(elev=22, azim=-125)
    ax.set_title(f"Akış çizgileri (renk: |U| / U∞) · α = {alpha:g}°", color=INK)
    fig.colorbar(cm.ScalarMappable(norm=norm, cmap="viridis"), ax=ax, shrink=0.6, pad=0.02, label="|U| / U∞")
    f = out / "streamlines_3d.png"
    fig.savefig(f, dpi=130, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    files.append(f.name)
    return files


# --------------------------------------------------------------------------- ana giriş


def _round(o, nd=5):
    if isinstance(o, dict):
        return {k: _round(v, nd) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_round(v, nd) for v in o]
    if isinstance(o, np.ndarray):
        if o.dtype.kind in "iu":
            return o.tolist()
        a = np.round(o.astype(float), nd)
        return [None if not np.isfinite(v) else v for v in a.ravel().tolist()] if a.ndim == 1 else \
            [[None if not np.isfinite(v) else v for v in row] for row in a.tolist()]
    if isinstance(o, (np.floating,)):
        return round(float(o), nd)
    return o


def build_visualization(case: str | Path, log=print) -> dict:
    """Bir vaka için tüm görselleştirme verilerini ve görüntüleri üretir."""
    case = Path(case)
    out = case / "viz"
    out.mkdir(exist_ok=True)
    t0 = time.time()
    fc = FoamCase(case)
    log(f"  ağ ve alanlar okundu: {fc.ncells:,} hücre ({fc.load_time:.1f} s)")
    surf = surface_data(fc)
    slices = standard_slices(fc)
    log(f"  yüzey ve {len(slices)} kesit hazır")
    stream = streamlines_3d(fc)
    log(f"  {len(stream['lines'])} akış çizgisi hesaplandı")
    (out / "surface.json").write_text(json.dumps(_round(surf)))
    (out / "slices.json").write_text(json.dumps(_round([{k: v for k, v in s.items()} for s in slices], 4)))
    (out / "streamlines.json").write_text(json.dumps(_round({"lines": stream["lines"]}, 4)))
    images = render_images(fc, surf, slices, stream, out)
    meta = {"alpha_deg": fc.info["alpha_deg"], "cells": fc.ncells, "time": fc.time, "images": images,
            "slices": [{"name": s["name"], "label": s["label"]} for s in slices],
            "cp_range": surf["cp_range"], "elapsed_s": time.time() - t0}
    (out / "meta.json").write_text(json.dumps(meta))
    log(f"  görselleştirme tamamlandı ({meta['elapsed_s']:.1f} s, {len(images)} görüntü)")
    return meta
