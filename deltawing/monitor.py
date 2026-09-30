"""Çalışan bir OpenFOAM vakasının canlı durumu: aşama, iterasyon, artıklar, katsayılar."""

from __future__ import annotations

import re
from pathlib import Path

STAGES = [
    ("log.blockMesh", "Arka plan ağı (blockMesh)", 0.02),
    ("log.surfaceFeatureExtract", "Keskin kenarlar (surfaceFeatureExtract)", 0.04),
    ("log.decomposePar.mesh", "Ayrıştırma (decomposePar)", 0.05),
    ("log.snappyHexMesh", "Ağ üretimi (snappyHexMesh)", 0.08),
    ("log.checkMesh", "Ağ kalite kontrolü (checkMesh)", 0.20),
    ("log.simpleFoam", "Akış çözümü (simpleFoam)", 0.22),
    ("log.reconstructParMesh", "Birleştirme (reconstructPar)", 0.97),
]

_RES = re.compile(r"Solving for (\w+), Initial residual = ([0-9.eE+-]+)")
_TIME = re.compile(r"^Time = ([0-9.eE+-]+)", re.M)
_CELLS = re.compile(r"cells:\s+(\d+)")


def _tail_cells(text: str):
    m = _CELLS.findall(text)
    return int(m[-1]) if m else None


def case_status(case: str | Path, end_time: int | None = None, max_points: int = 600) -> dict:
    case = Path(case)
    out: dict = {"stage": "Hazırlanıyor", "fraction": 0.0, "iteration": 0, "end_time": end_time,
                 "residuals": {}, "coeffs": {}, "cells": None}
    if not case.exists():
        return out
    for fname, label, frac in STAGES:
        if (case / fname).exists():
            out["stage"], out["fraction"] = label, frac
    ck = case / "log.checkMesh"
    if ck.exists():
        out["cells"] = _tail_cells(ck.read_text(errors="replace"))
    elif (case / "log.snappyHexMesh").exists():
        out["cells"] = _tail_cells((case / "log.snappyHexMesh").read_text(errors="replace"))

    log = case / "log.simpleFoam"
    if log.exists():
        text = log.read_text(errors="replace")
        blocks = _TIME.split(text)
        # blocks: [önce, t1, blok1, t2, blok2, ...]
        its, res = [], {}
        for i in range(1, len(blocks) - 1, 2):
            it = float(blocks[i])
            seen = {}
            for name, val in _RES.findall(blocks[i + 1]):
                if name not in seen:  # her iterasyonda ilk çözüm (p için ilk düzeltici)
                    seen[name] = float(val)
            if seen:
                its.append(it)
                for k in set(res) | set(seen):
                    res.setdefault(k, [None] * (len(its) - 1)).append(seen.get(k))
        if its:
            step = max(1, len(its) // max_points)
            out["iteration"] = int(its[-1])
            out["residuals"] = {"iter": its[::step], **{k: v[::step] for k, v in res.items()}}
            if end_time:
                out["fraction"] = 0.22 + 0.75 * min(1.0, its[-1] / end_time)
        if "End" in text[-200:]:
            out["fraction"] = max(out["fraction"], 0.97)

    files = sorted((case / "postProcessing").glob("forceCoeffs*/*/coefficient.dat")) + \
        sorted((case / "postProcessing").glob("forceCoeffs*/*/forceCoeffs.dat"))
    if files:
        names, rows = None, []
        for line in files[-1].read_text(errors="replace").splitlines():
            if line.startswith("#"):
                toks = line.lstrip("#").split()
                if toks and toks[0] == "Time":
                    names = toks
                continue
            try:
                rows.append([float(x) for x in line.split()])
            except ValueError:
                pass
        if names and rows:
            idx = {n: i for i, n in enumerate(names)}
            step = max(1, len(rows) // max_points)
            sel = rows[::step]
            out["coeffs"] = {"iter": [r[0] for r in sel],
                             "CL": [r[idx["Cl"]] for r in sel if len(r) > idx["Cl"]],
                             "CD": [r[idx["Cd"]] for r in sel if len(r) > idx["Cd"]]}
    return out
