"""OpenFOAM çözüm alanlarından kanat kuvvetlerini doğrudan hesaplama.

OpenFOAM ``forces`` fonksiyon nesnesiyle aynı formülasyon:
    basınç:  F_p = sum( rho * p_f * S_f )
    viskoz:  F_v = sum( rho * (nu + nut_w) * (U_P - U_w)_t / d_n * |S_f| )
S_f alan vektörü akış bölgesinden dışarı (gövdenin içine) bakar.

Fonksiyon nesneleri çalışmayan kurulumlar (örn. Ubuntu 24.04 'openfoam' 1912
paketi) için yedek yol ve forceCoeffs sonuçları için bağımsız doğrulamadır.
ASCII formatlı (writeFormat ascii) yeniden birleştirilmiş vaka gerektirir.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

_HEADER_END = re.compile(r"^\s*(\d+)\s*\n\s*\(", re.M)


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _list_body(text: str, start: int = 0) -> tuple[int, str, int]:
    """``N ( ... )`` listesinin eleman sayısı, gövdesi ve bitiş indeksi."""
    m = _HEADER_END.search(text, start)
    if m is None:
        raise ValueError("OpenFOAM listesi bulunamadı (ASCII format gerekli)")
    n = int(m.group(1))
    i = m.end()
    depth, j = 1, i
    while depth:
        c = text[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        j += 1
    return n, text[i: j - 1], j


def _read_body(path: Path) -> str:
    text = path.read_text(errors="replace")
    if "format      binary" in text[:2000] or "format binary" in text[:2000]:
        raise ValueError(f"{path} binary formatta; controlDict'te writeFormat ascii olmalı")
    return _strip_comments(text)


def read_points(poly: Path) -> np.ndarray:
    n, body, _ = _list_body(_read_body(poly / "points"))
    return np.array(body.replace("(", " ").replace(")", " ").split(), dtype=float).reshape(n, 3)


def read_labels(path: Path) -> np.ndarray:
    n, body, _ = _list_body(_read_body(path))
    return np.array(body.split(), dtype=np.int64)[:n]


def read_faces(poly: Path) -> list[np.ndarray]:
    _, body, _ = _list_body(_read_body(poly / "faces"))
    return [np.array(f.split(), dtype=np.int64) for f in re.findall(r"\d+\s*\(([^)]*)\)", body)]


def read_boundary(poly: Path) -> dict:
    text = _read_body(poly / "boundary")
    out = {}
    for m in re.finditer(r"(\w+)\s*\{([^}]*)\}", text):
        body = m.group(2)
        nf = re.search(r"nFaces\s+(\d+)", body)
        sf = re.search(r"startFace\s+(\d+)", body)
        if nf and sf:
            out[m.group(1)] = (int(sf.group(1)), int(nf.group(1)))
    return out


def _parse_values(body: str, ncomp: int) -> np.ndarray:
    arr = np.array(body.replace("(", " ").replace(")", " ").split(), dtype=float)
    return arr.reshape(-1, ncomp) if ncomp > 1 else arr


def read_field(path: Path, patch: str, ncomp: int, ncells: int):
    """(iç alan değerleri, yama değerleri veya None)."""
    text = _read_body(path)
    im = re.search(r"internalField\s+(uniform|nonuniform)", text)
    if im.group(1) == "uniform":
        val = re.search(r"internalField\s+uniform\s+([^;]+);", text).group(1)
        v = _parse_values(val, ncomp)
        internal = np.tile(v, (ncells, 1)) if ncomp > 1 else np.full(ncells, float(v))
    else:
        _, body, _ = _list_body(text, im.end())
        internal = _parse_values(body, ncomp)
    patch_val = None
    pm = re.search(r"\n\s*" + re.escape(patch) + r"\s*\{", text[text.find("boundaryField"):])
    if pm:
        seg_start = text.find("boundaryField") + pm.end()
        depth, j = 1, seg_start
        while depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        seg = text[seg_start: j - 1]
        vm = re.search(r"value\s+(uniform|nonuniform)", seg)
        if vm:
            if vm.group(1) == "uniform":
                v = _parse_values(re.search(r"value\s+uniform\s+([^;]+);", seg).group(1), ncomp)
                patch_val = v
            else:
                _, body, _ = _list_body(seg, vm.end())
                patch_val = _parse_values(body, ncomp)
    return internal, patch_val


def _face_geometry(pts: np.ndarray, face: np.ndarray):
    """OpenFOAM'daki gibi üçgen yelpazesi ile yüz merkezi ve alan vektörü."""
    p = pts[face]
    c0 = p.mean(axis=0)
    p1 = np.roll(p, -1, axis=0)
    tri_n = 0.5 * np.cross(p - c0, p1 - c0)
    tri_c = (p + p1 + c0) / 3.0
    mag = np.linalg.norm(tri_n, axis=1)
    sf = tri_n.sum(axis=0)
    cf = (tri_c * mag[:, None]).sum(axis=0) / max(mag.sum(), 1e-300)
    return cf, sf


def latest_time(case: Path) -> Path:
    times = []
    for d in case.iterdir():
        try:
            t = float(d.name)
        except ValueError:
            continue
        if t > 0 and (d / "p").exists():
            times.append((t, d))
    if not times:
        raise RuntimeError(f"Çözüm zaman klasörü bulunamadı: {case}")
    return max(times)[1]


def integrate_forces(case: str | Path, patch: str = "wing", rho: float = 1.225,
                     nu: float = 1.5e-5) -> dict:
    """Yarı model yama kuvvetleri (N): pressure, viscous, total vektörleri."""
    case = Path(case)
    poly = case / "constant" / "polyMesh"
    pts = read_points(poly)
    faces = read_faces(poly)
    owner = read_labels(poly / "owner")
    neigh = read_labels(poly / "neighbour")
    bnd = read_boundary(poly)
    start, nf = bnd[patch]
    ncells = int(max(owner.max(), neigh.max())) + 1
    tdir = latest_time(case)

    p_int, p_patch = read_field(tdir / "p", patch, 1, ncells)
    u_int, u_patch = read_field(tdir / "U", patch, 3, ncells)
    nut_patch = None
    if (tdir / "nut").exists():
        _, nut_patch = read_field(tdir / "nut", patch, 1, ncells)

    pfaces = np.arange(start, start + nf)
    pcells = owner[pfaces]
    # yama komşu hücrelerinin merkezleri (piramit ayrıştırması)
    need = np.zeros(ncells, dtype=bool)
    need[pcells] = True
    cell_faces: dict[int, list[int]] = {int(c): [] for c in np.unique(pcells)}
    for fi in np.nonzero(need[owner])[0]:
        cell_faces[int(owner[fi])].append(int(fi))
    for fi in np.nonzero(need[neigh])[0]:
        cell_faces[int(neigh[fi])].append(int(fi))
    geo_cache: dict[int, tuple] = {}

    def fgeo(fi):
        g = geo_cache.get(fi)
        if g is None:
            g = geo_cache[fi] = _face_geometry(pts, faces[fi])
        return g

    centres = {}
    for c, fl in cell_faces.items():
        cfs = np.array([fgeo(f)[0] for f in fl])
        sfs = np.array([fgeo(f)[1] for f in fl])
        c0 = cfs.mean(axis=0)
        vol = np.abs(np.einsum("ij,ij->i", sfs, cfs - c0)) / 3.0
        cen = 0.75 * cfs + 0.25 * c0
        centres[c] = (vol[:, None] * cen).sum(axis=0) / max(vol.sum(), 1e-300)

    fp = np.zeros(3)
    fv = np.zeros(3)
    for k, fi in enumerate(pfaces):
        cf, sf = fgeo(int(fi))
        c = int(pcells[k])
        pf = p_patch[k] if isinstance(p_patch, np.ndarray) and p_patch.ndim == 1 and len(p_patch) == nf else p_int[c]
        fp += rho * pf * sf
        area = np.linalg.norm(sf)
        n = sf / area
        d = abs(np.dot(centres[c] - cf, n))
        du = u_int[c]
        du_t = du - np.dot(du, n) * n
        nut_w = 0.0
        if nut_patch is not None:
            nut_w = float(nut_patch[k] if np.ndim(nut_patch) and len(np.atleast_1d(nut_patch)) == nf else np.atleast_1d(nut_patch)[0])
        fv += rho * (nu + nut_w) * du_t / max(d, 1e-300) * area
    return {"pressure": fp, "viscous": fv, "total": fp + fv, "time": float(tdir.name),
            "n_faces": int(nf)}
